import logging
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from werkzeug.utils import secure_filename
import os
import uuid
import json
import re

from resume_ai_analyzer import (
    # New and Corrected Functions for the Stable Strategy
    extract_resume_sections_safely,
    generate_stable_ats_report,
    generate_targeted_fix,
    calculate_new_score,

    # Existing Utility and Other Functions
    analyze_resume_with_openai,
    extract_text_from_pdf,
    extract_text_from_docx,
    extract_text_from_resume,
    check_ats_compatibility,
    check_ats_compatibility_fast,
    check_ats_compatibility_deep,
    extract_keywords_from_jd,
    compare_resume_with_keywords,
    analyze_job_description,
    fix_resume_formatting,
    generate_resume_summary,
    generate_michelle_template_html
)

try:
    from docx import Document
    from docx.shared import Pt, Inches, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.oxml import parse_xml
    from docx.oxml.ns import nsdecls
except ImportError as e:
    logging.error(f"Failed to import python-docx: {str(e)}")
    raise

try:
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.lib.colors import HexColor
except ImportError as e:
    logging.error(f"Failed to import reportlab: {str(e)}")
    raise

try:
    import fitz  # PyMuPDF for PDF text extraction
    import PyPDF2  # For PDF validation (encryption check)
    import pdfkit  # For DOCX to PDF conversion
except ImportError as e:
    logging.error(f"Failed to import required dependencies: {str(e)}")
    raise
import io  # Required for BytesIO in DOCX conversion
import tempfile  # Required for temporary file handling

logging_level = logging.INFO if os.environ.get("FLASK_ENV") != "development" else logging.DEBUG
logging.basicConfig(level=logging_level)
logger = logging.getLogger(__name__)

logging.getLogger("pdfminer").setLevel(logging.ERROR)

logger.info("Starting Flask app initialization...")

app = Flask(__name__, static_url_path='/static')
CORS(app, resources={r"/*": {"origins": "https://resumefixerpro.com"}})

UPLOAD_FOLDER = '/tmp/Uploads'  # Use /tmp for Render compatibility
STATIC_FOLDER = '/tmp/static'
try:
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    os.makedirs(STATIC_FOLDER, exist_ok=True)
    logger.info(f"Directories created: {UPLOAD_FOLDER}, {STATIC_FOLDER}")
except Exception as e:
    logger.error(f"Failed to create directories: {str(e)}")
    raise RuntimeError(f"Failed to create directories: {str(e)}")
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Removes unnecessary personal details from resume text
def remove_unnecessary_personal_info(text):
    lines = text.split('\n')
    filtered_lines = []
    flagged_terms = ['Marital Status', 'Date of Birth', 'Gender', 'Nationality', 'Religion']
    for line in lines:
        if not any(term.lower() in line.lower() for term in flagged_terms):
            filtered_lines.append(line)
    return '\n'.join(filtered_lines)


def cleanup_file(filepath):
    try:
        if os.path.exists(filepath):
            os.remove(filepath)
            logger.debug(f"Cleaned up file: {filepath}")
    except Exception as e:
        logger.error(f"Error cleaning up file: {str(e)}")

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({"status": "healthy", "message": "OK"}), 200

@app.route("/upload", methods=["POST"])
def upload_resume():
    try:
        file = request.files.get("file")
        if not file:
            return jsonify({"error": "No file uploaded"}), 400

        # ✅ Extract text directly from uploaded file
        text = extract_text_from_resume(file)

        if not text.strip():
            return jsonify({"error": "Failed to extract resume text"}), 500

        # ✅ Smart section parser - this is already here, which is great!
        parsed_sections = extract_resume_sections(text)

        # ✅ Optional summary generation
        name = parsed_sections.get("name", "")
        role = parsed_sections.get("job_title", "")
        experience = parsed_sections.get("work_experience", "")
        skills = parsed_sections.get("skills", "")
        summary = generate_resume_summary(name, role, experience, skills)
        parsed_sections["summary"] = summary

        # ✅ ATS report and score (THIS IS THE CORRECTED LINE)
        ats_result = generate_ats_report(text, parsed_sections)
        ats_issues = ats_result["issues"]
        score = ats_result["score"]

        return jsonify({
            "resume_text": text,
            "parsedResumeContent": parsed_sections,
            "ats_report": ats_issues,
            "score": score
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    
@app.route('/main-upload', methods=['POST'])
def main_upload():
    try:
        logger.info("Received request for /main-upload")
        file = request.files.get('file')
        if not file or file.filename == '':
            logger.error("No file uploaded in request")
            return jsonify({"error": "No file uploaded"}), 400

        logger.info(f"File received: {file.filename}, size: {file.content_length}")
        ext = os.path.splitext(file.filename)[1].lower()
        if ext not in {'.pdf', '.docx'}:
            logger.error(f"Unsupported file format: {ext}")
            return jsonify({"error": f"Unsupported file format: {ext}. Please upload a PDF or DOCX."}), 400

        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        logger.info(f"Saving file to {filepath}")
        file.save(filepath)

        text = extract_text_from_pdf(filepath) if ext == ".pdf" else extract_text_from_docx(filepath)
        cleaned_text = remove_unnecessary_personal_info(text)

        job_title = request.form.get('job_title', '')
        company_name = request.form.get('company_name', '')
        logger.info(f"Job title: {job_title}, Company name: {company_name}")

        prompt = f"""
You are a professional resume editor. Fix the following resume to make it professional, concise, and tailored for the role of {job_title} at {company_name}. Remove unnecessary personal info (e.g., marital status, date of birth, gender, nationality, religion). Use active voice, quantify achievements where possible, and ensure ATS compatibility.

Resume:
{cleaned_text[:6000]}

Return the improved resume as plain text.
"""

        from openai import OpenAI
        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        logger.info("Sending request to OpenAI")
        ai_resp = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
            temperature=0
        )

        improved_resume = ai_resp.choices[0].message.content.strip()
        logger.info("Received response from OpenAI")

        # ✅ Return plain text as JSON
        return jsonify({"success": True, "data": {"text": improved_resume}})

    except Exception as e:
        logger.error(f"Error in /main-upload: {str(e)}")
        return jsonify({"error": f"Failed to process resume: {str(e)}"}), 500

    finally:
        try:
            if 'filepath' in locals() and os.path.exists(filepath):
                cleanup_file(filepath)
        except Exception as cleanup_err:
            logger.warning(f"Cleanup failed: {cleanup_err}")
  

@app.route('/ats-check', methods=['POST'])
def check_ats():
    try:
        file = request.files.get('file')  # Only look for the key "file"
        if not file or file.filename == '':
            return jsonify({"error": "No file uploaded"}), 400

        ext = os.path.splitext(file.filename)[1].lower()
        if ext not in {'.pdf', '.docx'}:
            return jsonify({"error": f"Unsupported file format: {ext}. Please upload a PDF or DOCX."}), 400

        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)

        text = extract_text_from_pdf(filepath) if ext == ".pdf" else extract_text_from_docx(filepath)
        cleaned_text = remove_unnecessary_personal_info(text)

        # Enhanced ATS prompt with personal info warning
        prompt = f"""
You are an ATS expert. Review the following resume and identify up to 5 ATS-related issues.

⚠️ Also flag any unnecessary personal information such as:
- Marital Status
- Date of Birth
- Gender
- Nationality
- Religion

These are not required in a professional resume and should be removed for better ATS compatibility.

Resume:
{cleaned_text[:6000]}

Return your output as a list like this:
["✅ Passed: ...", "❌ Issue: ..."]
Only include meaningful points.
"""

        from openai import OpenAI
        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        ai_resp = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
            temperature=0
        )

        feedback = ai_resp.choices[0].message.content.strip().splitlines()

        # Normalize feedback
        formatted_feedback = []
        for line in feedback:
            line = line.strip()
            if not line:
                continue
            if line.lower().startswith("issue:"):
                formatted_feedback.append("❌ " + line[len("issue:"):].strip())
            elif line.lower().startswith("passed:"):
                formatted_feedback.append("✅ " + line[len("passed:"):].strip())
            elif line.startswith("❌") or line.startswith("✅"):
                formatted_feedback.append(line)
            else:
                formatted_feedback.append("❌ " + line)

        # Score calculation
        score = 100 - (len([line for line in formatted_feedback if line.startswith("❌")]) * 20)
        score = max(0, min(score, 100))

        return jsonify({"issues": formatted_feedback, "score": score})

    except Exception as e:
        logger.error(f"Error in /ats-check: {str(e)}")
        return jsonify({"error": f"Failed to check ATS compatibility: {str(e)}"}), 500

    finally:
        try:
            if 'filepath' in locals() and os.path.exists(filepath):
                cleanup_file(filepath)
        except Exception as cleanup_err:
            logger.warning(f"Cleanup failed: {cleanup_err}")

@app.route('/analyze', methods=['POST'])
def analyze_resume():
    try:
        data = request.get_json()
        resume_text = data.get('resume_text')
        if not resume_text or not isinstance(resume_text, str) or not resume_text.strip():
            return jsonify({"error": "Invalid or empty resume text"}), 400
        result = analyze_resume_with_openai(resume_text, atsfix=False)
        if "error" in result:
            return jsonify({"error": "Unable to generate suggestions. Please check if the API key is set."}), 500
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error in /analyze: {str(e)}")
        return jsonify({"error": f"Failed to analyze resume: {str(e)}"}), 500

@app.route('/fix-suggestion', methods=['POST'])
def fix_suggestion():
    try:
        if 'payload' not in request.form:
            return jsonify({"success": False, "error": "Missing payload in request form"}), 400
            
        data = json.loads(request.form.get('payload'))
        
        # Get all the necessary data from the frontend payload
        suggestion = data.get("suggestion")
        current_score = data.get("current_score")
        section_name_to_fix = data.get("section_name")
        section_content = data.get("section_content")
        full_resume_text = data.get("full_resume_text")

        # Check if all required data is present
        if not all([suggestion, isinstance(current_score, int), section_name_to_fix, section_content, full_resume_text]):
            logger.error(f"Incomplete payload received in /fix-suggestion: {data}")
            return jsonify({"success": False, "error": "Incomplete data sent from the client."}), 400

        # Call the AI to get a targeted fix for the specific section
        fix_result = generate_targeted_fix(suggestion, section_name_to_fix, section_content, full_resume_text)
        
        # --- NEW SAFETY CHECK ---
        # This is the most important change. It checks if the AI returned a valid, non-empty fix.
        if 'error' in fix_result or not fix_result.get("fixedContent"):
            logger.error(f"AI returned empty or invalid content for the fix: {fix_result}")
            return jsonify({"success": False, "error": "AI could not generate a valid improvement. Please try again or skip."}), 500

        # Calculate the new score based on predictable rules
        new_score = calculate_new_score(current_score, suggestion)
        
        # Combine the successful results into the final response
        final_response = {
            "section": fix_result["section"],
            "fixedContent": fix_result["fixedContent"],
            "newScore": new_score
        }
        
        return jsonify({"success": True, "data": final_response})

    except Exception as e:
        import traceback
        logger.error(f"Error in /fix-suggestion: {traceback.format_exc()}")
        return jsonify({"success": False, "error": "An unexpected server error occurred."}), 500

@app.route('/preview-resume', methods=['POST'])
def preview_resume():
    try:
        data = request.get_json()
        sections = data.get('sections')
        if not sections:
            logger.error("No sections provided in /preview-resume request")
            return jsonify({"error": "No sections provided"}), 400

        # Generate HTML
        html_content = """
        <div style='width: 90%; margin: 0 auto; font-family: Arial, sans-serif;'>
        """
        for section, content in sections.items():
            section_title = section.replace('_', ' ').title()
            html_content += """
            <div class='section' style='margin-bottom: 1.2rem;'>
              <h3 style='font-size: 0.95rem; line-height: 1.3; color: #222; margin-bottom: 4px; border-bottom: 1px solid #ccc;'>{}</h3>
              <div style='line-height: 1.5;'>{}</div>
            </div>
            """.format(section_title.upper(), content.replace('\n', '<br>'))
        html_content += "</div>"

        # Generate plain text
        resume_text_lines = []
        for section, content in sections.items():
            section_title = section.replace('_', ' ').title()
            resume_text_lines.append(section_title.upper())
            lines = content.split('\n')
            for line in lines:
                line = line.strip()
                if line:
                    if not line.startswith('- '):
                        line = '- ' + line
                    resume_text_lines.append(line)
            resume_text_lines.append('')

        # Clean up excessive blank lines
        cleaned_resume = []
        last_was_empty = False
        for line in resume_text_lines:
            if line == '' and last_was_empty:
                continue
            cleaned_resume.append(line)
            last_was_empty = (line == '')
        resume_text = '\n'.join(cleaned_resume).strip()

        return jsonify({"preview_text": resume_text, "preview_html": html_content})

    except Exception as e:
        logger.error(f"Error in /preview-resume: {str(e)}")
        return jsonify({"error": f"Failed to generate preview: {str(e)}"}), 500

@app.route('/final-resume', methods=['POST'])
def final_resume_download():
    try:
        data = request.get_json()
        html_content = data.get("html")
        file_format = data.get("format", "pdf")

        if not html_content:
            logger.error("No HTML content provided in /final-resume request")
            return jsonify({"error": "Invalid HTML content provided"}), 400

        wrapped_html = """
        <div style='width: 90%; margin: 0 auto; font-family: Arial, sans-serif; padding: 20px;'>
            {}
        </div>
        """.format(html_content)

        if file_format in ["pdf", "docx"]:
            if file_format == "pdf":
                with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as pdf_file:
                    pdfkit.from_string(wrapped_html, pdf_file.name)
                    logger.info(f"Generated PDF at: {pdf_file.name}")
                    return send_file(
                        pdf_file.name,
                        as_attachment=True,
                        download_name="Final_Resume.pdf",
                        mimetype='application/pdf'
                    )

            else:
                try:
                    from html2docx import html2docx
                except ImportError as e:
                    logger.error(f"html2docx not installed: {str(e)}")
                    return jsonify({"error": "Server error: html2docx not installed"}), 500

                docx_bytes = html2docx(wrapped_html)
                logger.info("Generated DOCX in memory")
                return send_file(
                    io.BytesIO(docx_bytes),
                    as_attachment=True,
                    download_name="Final_Resume.docx",
                    mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
                )

        else:
            logger.error(f"Invalid format requested: {file_format}")
            return jsonify({"error": "Invalid format. Use 'pdf' or 'docx'."}), 400

    except Exception as e:
        logger.error(f"Error in /final-resume: {str(e)}")
        return jsonify({"error": f"Failed to generate final resume: {str(e)}"}), 500

@app.route('/generate-cover-letter', methods=['POST'])
def generate_cover_letter():
    file = request.files.get('file') or request.files.get('resume')
    job_title = request.form.get('job_title')
    company_name = request.form.get('company_name')

    if not file or not job_title or not company_name:
        return jsonify({"error": "File, job title, and company name are required"}), 400

    filename = secure_filename(file.filename)
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    try:
        file.save(filepath)
        resume_text = extract_text_from_resume(file)
        if not resume_text.strip():
            return jsonify({"error": "Could not extract text from resume"}), 400

        prompt = """
You are a professional cover letter writer. Write a concise cover letter (300-400 words) for the following details:
Job Title: {}
Company Name: {}
Resume: {}
Include a greeting, an introduction, a body highlighting relevant skills and experiences, and a closing statement.
        """.format(job_title, company_name, resume_text[:6000])

        try:
            from openai import OpenAI
            client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
            response = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "user", "content": prompt}]
            )
            cover_letter = response.choices[0].message.content.strip()
            return jsonify({"cover_letter": cover_letter})
        except Exception as e:
            logger.error(f"Error in OpenAI API call for /generate-cover-letter: {str(e)}")
            return jsonify({"error": f"Failed to generate cover letter: {str(e)}"}), 500
    except Exception as e:
        logger.error(f"Error in /generate-cover-letter: {str(e)}")
        return jsonify({"error": f"Failed to generate cover letter: {str(e)}"}), 500
    finally:
        cleanup_file(filepath)

@app.route('/download-cover-letter', methods=['POST'])
def download_cover_letter():
    data = request.get_json()
    cover_letter = data.get('cover_letter')
    if not cover_letter:
        return jsonify({"error": "No cover letter provided"}), 400

    output_path = os.path.join(app.config['UPLOAD_FOLDER'], f"cover_letter_{uuid.uuid4()}.docx")
    try:
        doc = Document()
        doc.add_heading("Cover Letter", level=1)
        for line in cover_letter.splitlines():
            line = line.strip()
            if line:
                doc.add_paragraph(line)
        doc.save(output_path)
        return send_file(
            output_path,
            as_attachment=True,
            download_name="Cover_Letter.docx",
            mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        )
    except Exception as e:
        logger.error(f"Error in /download-cover-letter: {str(e)}")
        return jsonify({"error": f"Failed to download cover letter: {str(e)}"}), 500
    finally:
        cleanup_file(output_path)

@app.route('/resume-score', methods=['POST'])
def resume_score():
    resume_text = ""
    filepath = None  # For cleanup later

    try:
        if request.is_json:
            data = request.get_json()
            resume_text = data.get("resume_text", "")
        else:
            file = request.files.get('file')
            if not file:
                return jsonify({"error": "No file uploaded"}), 400
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(file.filename))
            file.save(filepath)
            ext = os.path.splitext(filepath)[1].lower()
            if ext == ".pdf":
                resume_text = extract_text_from_pdf(filepath)
            elif ext == ".docx":
                resume_text = extract_text_from_docx(filepath)
            else:
                return jsonify({"error": "Unsupported file format"}), 400

        if not resume_text.strip():
            return jsonify({"error": "No extractable text found in resume"}), 400

        prompt = f"""
You are a professional resume reviewer. Give a resume score between 0 and 100 based on:
- Formatting and readability
- Grammar and professionalism
- Use of action verbs and achievements
- Keyword optimization for ATS
- Overall impression and completeness
Resume:
{resume_text[:6000]}
Just return a number between 0 and 100, nothing else.
        """

        from openai import OpenAI
        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a strict but fair resume scoring assistant."},
                {"role": "user", "content": prompt}
            ]
        )
        score_raw = response.choices[0].message.content.strip()
        score = int(''.join(filter(str.isdigit, score_raw)))
        return jsonify({"score": max(0, min(score, 100))})

    except Exception as e:
        logger.error(f"Error in /resume-score: {str(e)}")
        return jsonify({"score": 70})
    finally:
        if filepath:
            cleanup_file(filepath)

@app.route('/optimize-keywords', methods=['POST'])
def optimize_keywords():
    resume_file = request.files.get('resume')
    job_description = request.form.get('job_description', '')

    if not resume_file or not job_description:
        return jsonify({"error": "Missing resume or job description"}), 400

    resume_text = extract_text_from_resume(resume_file)
    if not resume_text.strip():
        return jsonify({"error": "No extractable text found in resume"}), 400

    jd_keywords = extract_keywords_from_jd(job_description)
    if not jd_keywords:
        return jsonify({"error": "No keywords extracted from job description"}), 400

    results = compare_resume_with_keywords(resume_text, jd_keywords)
    return jsonify(results)

@app.route('/generate-ai-resume', methods=['POST'])
def generate_ai_resume():
    try:
        data = request.json
        name = data.get("name", "")
        email = data.get("email", "")
        phone = data.get("phone", "")
        location = data.get("location", "")
        education = data.get("education", "")
        experience = data.get("experience", "")
        skills = data.get("skills", "")
        certifications = data.get("certifications", "")
        languages = data.get("languages", "")
        hobbies = data.get("hobbies", "")
        summary = data.get("summary", "")

        def generate_section_content(section_name, user_input, context=""):
            if not user_input.strip():
                return ""
            prompts = {
                "summary": """
You are a resume writing assistant. Based on the following:
Education: {}
Experience: {}
Skills: {}
Write a 2-3 line professional summary for a resume.
""".format(education, experience, skills),
                "education": """
You are a resume writing assistant. The user has provided the following education details: '{}'.
Based on this, generate a professional education entry for a resume. Include degree, institution, and years (e.g., 2020-2024). If details are missing, make reasonable assumptions.
Format the output as plain text, e.g.:
B.Tech in Computer Science, XYZ University, 2020-2024
""".format(user_input),
                "experience": """
You are a resume writing assistant. The user has provided the following experience details: '{}'.
Based on this, generate a professional experience entry for a resume. Include job title, company, duration (e.g., June 2023 - August 2023), and a brief description of responsibilities (1-2 lines).
Format the output as plain text, e.g.:
Software Intern, ABC Corp, June 2023 - August 2023
Developed web applications using React and Node.js
""".format(user_input),
                "skills": """
You are a resume writing assistant. The user has provided the following skills: '{}'.
Based on this, generate a professional skills section for a resume. Expand the list by adding 2-3 relevant skills if possible, and format as a bullet list.
Format the output as plain text with bullet points, e.g.:
- Python
- JavaScript
- SQL
""".format(user_input),
                "certifications": """
You are a resume writing assistant. The user has provided the following certifications: '{}'.
Based on this, generate a professional certifications section for a resume. Include the certification name, issuing organization, and year (e.g., 2023). If details are missing, make reasonable assumptions.
Format the output as plain text, e.g.:
Certified Python Developer, XYZ Institute, 2023
""".format(user_input),
                "languages": """
You are a resume writing assistant. The user has provided the following languages: '{}'.
Based on this, generate a professional languages section for a resume. Include proficiency levels (e.g., Fluent, Intermediate) and format as a list.
Format the output as plain text, e.g.:
English (Fluent)
Spanish (Intermediate)
""".format(user_input),
                "hobbies": """
You are a resume writing assistant. The user has provided the following hobbies: '{}'.
Based on this, generate a professional hobbies section for a resume. Expand with 1-2 related hobbies if possible, and format as a bullet list.
Format the output as plain text with bullet points, e.g.:
- Reading
- Hiking
""".format(user_input)
            }
            prompt = prompts.get(section_name, "")
            if not prompt:
                return user_input
            try:
                from openai import OpenAI
                client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
                res = client.chat.completions.create(
                    model="gpt-3.5-turbo",
                    messages=[{"role": "user", "content": prompt}]
                )
                content = res.choices[0].message.content.strip()
                if isinstance(content, list):
                    content = '\n'.join(content).strip()
                return content
            except Exception as e:
                logger.error(f"Error generating {section_name}: {str(e)}")
                return user_input

        if summary:
            summary = generate_section_content("summary", summary)
        else:
            prompt = """
You are a resume writing assistant. Based on the following:
Education: {}
Experience: {}
Skills: {}
Write a 2-3 line professional summary for a resume.
""".format(education, experience, skills)
            try:
                from openai import OpenAI
                client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
                res = client.chat.completions.create(
                    model="gpt-3.5-turbo",
                    messages=[{"role": "user", "content": prompt}]
                )
                summary = res.choices[0].message.content.strip()
                if isinstance(summary, list):
                    summary = str(summary).strip()
            except Exception as e:
                logger.error(f"Error generating summary: {str(e)}")
                summary = "Unable to generate summary. Please check if the API key is correctly set."

        education = generate_section_content("education", education)
        experience = generate_section_content("experience", experience)
        skills = generate_section_content("skills", skills)
        certifications = generate_section_content("certifications", certifications)
        languages = generate_section_content("languages", languages)
        hobbies = generate_section_content("hobbies", hobbies)

        def section_html(title, content):
            if not content.strip():
                return ""
            html_content = content.strip().replace('\n', '<br>')
            return """
            <div class='section' style='margin-bottom: 1.2rem;'>
              <h3 style='font-size: 0.95rem; line-height: 1.3; color: #222; margin-bottom: 4px; border-bottom: 1px solid #ccc;'>- {}</h3>
              <div>{}</div>
            </div>
            """.format(title.upper(), html_content)

        top = """
        <div style='text-align: center; margin-bottom: 1.2rem;'>
          <div style='font-size: 1.3rem; font-weight: bold; color: #1D75E5;'>{}</div>
          <div style='font-size: 0.9rem; color: #333;'>{}</div>
        </div>
        """.format(name, f"{email} | {phone} | {location}")

        html = top
        html += section_html("Professional Summary", summary)
        html += section_html("Education", education)
        html += section_html("Experience", experience)
        html += section_html("Skills", skills)
        html += section_html("Certifications", certifications)
        html += section_html("Languages", languages)
        html += section_html("Hobbies", hobbies)

        return jsonify({"success": True, "html": html})

    except Exception as e:
        logger.error(f"Error in /generate-ai-resume: {str(e)}")
        return jsonify({"error": f"Failed to generate AI resume: {str(e)}"}), 500

@app.route('/analyze-jd', methods=['POST'])
def analyze_jd():
    try:
        data = request.get_json()
        jd_text = data.get('job_description', '')
        if not jd_text:
            return jsonify({"error": "No job description provided"}), 400
        result = analyze_job_description(jd_text)
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error in /analyze-jd: {str(e)}")
        return jsonify({"error": f"Failed to analyze job description: {str(e)}"}), 500

@app.route('/convert-format', methods=['POST'])
def convert_format():
    try:
        file = request.files.get('file')
        target_format = request.form.get('target_format')

        if not file or not target_format:
            logger.error("Missing file or target format in request")
            return jsonify({"error": "Missing file or target format specified"}), 400

        ext = os.path.splitext(file.filename)[1].lower()
        if ext not in ['.pdf', '.docx']:
            logger.error(f"Unsupported file format: {ext}")
            return jsonify({"error": f"Invalid file format: {ext}. Please upload a PDF or DOCX file."}), 400

        filename = secure_filename(file.filename)
        upload_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        output_path = os.path.join(app.config['UPLOAD_FOLDER'], f"converted_{uuid.uuid4()}.{target_format}")
        html_temp_path = os.path.join(app.config['UPLOAD_FOLDER'], f"temp_{uuid.uuid4()}.html")

        file.save(upload_path)
        logger.debug(f"Saved uploaded file to {upload_path}")

        if not os.path.exists(upload_path):
            logger.error(f"File not found after saving: {upload_path}")
            return jsonify({"error": "File could not be saved properly"}), 500

        os.chmod(upload_path, 0o644)
        file_size = os.path.getsize(upload_path) / 1024
        if file_size == 0:
            logger.error(f"Empty file uploaded: {filename}")
            return jsonify({"error": "Uploaded file is empty"}), 400

        logger.debug(f"File size: {file_size:.2f} KB")

        if ext == '.pdf':
            try:
                with open(upload_path, 'rb') as f:
                    pdf_reader = PyPDF2.PdfReader(f)
                    if pdf_reader.is_encrypted:
                        logger.error("Uploaded PDF is encrypted")
                        return jsonify({"error": "PDF is encrypted and cannot be processed."}), 400
            except Exception as e:
                logger.error(f"Invalid or corrupted file: {str(e)}")
                return jsonify({"error": f"Invalid or corrupted file: {str(e)}"}), 400

        if ext == '.docx':
            try:
                doc = Document(upload_path)
            except Exception as e:
                logger.error(f"Invalid or corrupted DOCX file: {str(e)}")
                return jsonify({"error": f"Invalid or corrupted DOCX file: {str(e)}"}), 400

        if target_format == 'text':
            text = ""
            if ext == '.pdf':
                try:
                    doc = fitz.open(upload_path)
                    if doc.page_count == 0:
                        logger.error("PDF has no pages")
                        return jsonify({"error": "PDF has no content to extract"}), 400
                    text = "\n".join([page.get_text().strip() for page in doc])
                    doc.close()
                    logger.info(f"Extracted text from PDF: {len(text)} characters")
                except Exception as e:
                    logger.error(f"Failed to extract text from PDF: {str(e)}")
                    return jsonify({"error": f"Failed to extract text from PDF: {str(e)}"}), 500

            elif ext == '.docx':
                try:
                    doc = Document(upload_path)
                    text = "\n".join([para.text for para in doc.paragraphs])
                    logger.info(f"Extracted text from DOCX: {len(text)} characters")
                except Exception as e:
                    logger.error(f"Failed to extract text from DOCX: {str(e)}")
                    return jsonify({"error": f"Failed to extract text from DOCX: {str(e)}"}), 500

            if not text.strip():
                logger.warning("No text could be extracted from the file")
                return jsonify({"error": "No text could be extracted. The file may be empty or contain only images."}), 400

            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(text)
            
            return send_file(
                output_path,
                as_attachment=True,
                download_name="extracted-text.txt",
                mimetype='text/plain'
            )

        elif ext == '.docx' and target_format == 'pdf':
            try:
                doc = Document(upload_path)
                paragraphs = [para.text for para in doc.paragraphs if para.text.strip()]
                if not paragraphs:
                    logger.error("DOCX file has no content")
                    return jsonify({"error": "DOCX file is empty or contains no readable text."}), 400

                html_content = '\n'.join([f"<p>{para}</p>" for para in paragraphs])
                
                with open(html_temp_path, 'w', encoding='utf-8') as f:
                    f.write(html_content)
                
                pdfkit.from_file(html_temp_path, output_path)
                
                return send_file(
                    output_path,
                    as_attachment=True,
                    download_name="converted.pdf",
                    mimetype='application/pdf'
                )

            except Exception as e:
                logger.error(f"DOCX to PDF conversion failed: {str(e)}")
                return jsonify({"error": f"Failed to convert DOCX to PDF: {str(e)}"}), 500

        elif ext == '.pdf' and target_format == 'docx':
            try:
                doc = fitz.open(upload_path)
                if doc.page_count == 0:
                    logger.error("PDF has no pages")
                    return jsonify({"error": "PDF has no content to convert"}), 400
                text = "\n".join([page.get_text().strip() for page in doc])
                doc.close()

                if not text.strip():
                    logger.warning("No text extracted from PDF")
                    return jsonify({"error": "No text could be extracted from the PDF."}), 400

                word_doc = Document()
                word_doc.add_paragraph(text)
                word_doc.save(output_path)
                logger.info(f"Converted PDF to DOCX: {output_path}")
                return send_file(
                    output_path,
                    as_attachment=True,
                    download_name="converted.docx",
                    mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
                )

            except Exception as e:
                logger.error(f"Failed to convert PDF to DOCX: {str(e)}")
                return jsonify({"error": f"Failed to convert PDF to DOCX: {str(e)}"}), 500

        else:
            logger.error(f"Unsupported conversion request: {ext} to {target_format}")
            return jsonify({"error": "Unsupported conversion request. Only PDF to DOCX, DOCX to PDF, or text extraction are supported."}), 400

    except Exception as e:
        logger.error(f"Error in /convert-format: {str(e)}")
        return jsonify({"error": f"Failed to process file: {str(e)}"}), 500
    finally:
        cleanup_file(upload_path)
        cleanup_file(output_path)
        cleanup_file(html_temp_path)

@app.route('/fix-formatting', methods=['POST'])
def fix_formatting():
    file = request.files.get('file') or request.files.get('resume')
    if not file:
        return jsonify({"error": "No file uploaded"}), 400

    filename = secure_filename(file.filename)
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)

    try:
        file.save(filepath)
        result = fix_resume_formatting(filepath)
        logger.info(f"Formatted resume at: {filepath}")
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error in /fix-formatting: {str(e)}")
        return jsonify({"error": f"Failed to process resume formatting: {str(e)}"}), 500
    finally:
        cleanup_file(filepath)

@app.route('/generate-resume-summary', methods=['POST'])
def generate_resume_summary_api():
    try:
        data = request.get_json()
        name = data.get('name', '')
        role = data.get('role', '')
        experience = data.get('experience', '')
        skills = data.get('skills', '')

        if not name or not role or not experience or not skills:
            return jsonify({"error": "Missing required fields"}), 400

        summary = generate_resume_summary(name, role, experience, skills)
        return jsonify({"summary": summary})

    except Exception as e:
        logger.error(f"Error in /generate-resume-summary: {str(e)}")
        return jsonify({"error": f"Failed to generate resume summary: {str(e)}"}), 500

@app.route('/send-feedback', methods=['POST'])
def send_feedback():
    try:
        data = request.form
        name = data.get('name', 'Unknown')
        email = data.get('email', '')
        message = data.get('message', '')
        file = request.files.get('screenshot')

        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        from email.mime.base import MIMEBase
        from email import encoders
        import smtplib

        sender_email = "help@resumefixerpro.com"
        receiver_email = "help@resumefixerpro.com"
        smtp_server = "smtp.hostinger.com"
        smtp_port = 587
        smtp_password = os.environ.get("SMTP_PASSWORD")

        msg = MIMEMultipart()
        msg['From'] = sender_email
        msg['To'] = receiver_email
        msg['Subject'] = 'Feedback Submission from ResumeFixerPro'

        body = """
Name: {}
Email: {}
Message:
{}
""".format(name, email, message)
        msg.attach(MIMEText(body, 'plain'))

        if file:
            filename = secure_filename(file.filename)
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)

            with open(filepath, 'rb') as attachment:
                part = MIMEBase('application', 'octet-stream')
                part.set_payload(attachment.read())

            encoders.encode_base64(part)
            part.add_header('Content-Disposition', f'attachment; filename={filename}')
            msg.attach(part)

            cleanup_file(filepath)

        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        server.login(sender_email, smtp_password)
        server.sendmail(sender_email, receiver_email, msg.as_string())
        server.quit()

        return jsonify({"success": True, "message": "Feedback sent successfully!"})

    except Exception as e:
        logger.error(f"Error sending feedback: {str(e)}")
        return jsonify({"error": f"Failed to send feedback: {str(e)}"}), 500

@app.route('/send-message', methods=['POST'])
def send_message():
    try:
        data = request.get_json()
        name = data.get('name', 'Unknown')
        email = data.get('email', '')
        message = data.get('message', '')

        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart
        import smtplib

        sender_email = "help@resumefixerpro.com"
        receiver_email = "help@resumefixerpro.com"
        smtp_server = "smtp.gmail.com"
        smtp_port = 587
        smtp_password = os.environ.get("SMTP_PASSWORD")

        msg = MIMEMultipart()
        msg['From'] = sender_email
        msg['To'] = receiver_email
        msg['Subject'] = 'Contact Message from ResumeFixerPro'

        body = """
New Contact Message:
Name: {}
Email: {}
Message:
{}
""".format(name, email, message)
        msg.attach(MIMEText(body, 'plain'))

        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        server.login(sender_email, smtp_password)
        server.send_message(msg)
        server.quit()

        return jsonify({"success": True, "message": "Message sent successfully!"})

    except Exception as e:
        logger.error(f"Error sending message: {str(e)}")
        return jsonify({"error": f"Failed to send message: {str(e)}"}), 500

@app.route('/extract-sections', methods=['POST'])
def extract_sections():
    try:
        data = request.get_json()
        text = data.get('text', '')

        if not text.strip():
            logger.warning("No resume text provided in /extract-sections request")
            return jsonify({"error": "No resume text provided"}), 400

        # ✅ Smart section extraction
        sections = extract_resume_sections(text)

        # ✅ Inject fallback empty keys if missing (optional)
        required_keys = ["name", "job_title", "contact", "summary", "education", "work_experience", "projects", "skills", "certifications", "languages", "linkedin", "achievements"]
        for key in required_keys:
            if key not in sections:
                sections[key] = ""

        # ✅ Check if anything found at all
        if not any(sections.values()):
            logger.warning("No sections could be extracted from the resume text")
            return jsonify({"error": "Failed to extract sections: Resume format may be unsupported or unstructured."}), 400

        logger.info(f"Successfully extracted sections: {list(sections.keys())}")
        return jsonify(sections)

    except Exception as e:
        logger.error(f"Error extracting sections: {str(e)} | Resume text: {text[:500]}")
        return jsonify({"error": f"Failed to extract sections: {str(e)}"}), 500

# =====================================================================
# FINAL API ENDPOINT FOR THE NEW WORDPRESS FRONTEND (CORRECTED INDENTATION)
# =====================================================================

@app.route('/api/v1/analyze-resume', methods=['POST'])
def analyze_resume_for_frontend():
    try:
        if 'resume_file' not in request.files:
            return jsonify({"success": False, "error": "No 'resume_file' part in the request."}), 400
        
        file = request.files['resume_file']
        if file.filename == '':
            return jsonify({"success": False, "error": "No file selected."}), 400

        text = extract_text_from_resume(file) # This helper function is still valid
        if not text:
            return jsonify({"success": False, "error": "Could not extract text from the resume."}), 500

        # STEP 1: Extract sections SAFELY without any changes.
        extracted_sections = extract_resume_sections_safely(text) # <<< बदला हुआ
        if not extracted_sections or extracted_sections.get("error"):
             return jsonify({"success": False, "error": extracted_sections.get("error", "Failed to parse resume sections.")}), 500

        # STEP 2: Generate a STABLE ATS report.
        ats_result = generate_stable_ats_report(text, extracted_sections) # <<< बदला हुआ

        # Format the data for the frontend
        passed_checks = ats_result.get("passed_checks", [])
        issues_to_fix = []
        raw_issues = ats_result.get("issues_to_fix", [])
        for i, issue_text in enumerate(raw_issues):
            issues_to_fix.append({
                "issue_id": f"err_{i+1}",
                "issue_text": issue_text
            })

        formatted_data = {
            "score": ats_result.get('score', 0),
            "analysis": {
                "passed_checks": passed_checks,
                "issues_to_fix": issues_to_fix
            },
            "extracted_data": extracted_sections 
        }
        
        return jsonify({"success": True, "data": formatted_data})

    except Exception as e:
        import traceback
        logger.error(f"Error in /api/v1/analyze-resume: {traceback.format_exc()}")
        return jsonify({"success": False, "error": "An unexpected server error occurred."}), 500
    
if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"Starting Flask app on port {port}")
    app.run(host="0.0.0.0", port=port)

logger.info("Flask app initialization complete.")
