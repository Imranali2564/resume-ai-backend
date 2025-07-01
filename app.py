import logging
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from werkzeug.utils import secure_filename
from openai import OpenAI
import os
import uuid
import json
import re
import io

# --- YEH SECTION UPDATE KIYA GAYA HAI ---
# Libraries for DOCX generation and PDF handling
from docx import Document
from docx.shared import Pt, Inches, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from bs4 import BeautifulSoup
import fitz  # PyMuPDF
import PyPDF2
import pdfkit
# html2docx ki ab zaroorat nahi hai, isliye hata diya gaya hai
# --------------------------------------

# Local application imports from resume_ai_analyzer.py
from resume_ai_analyzer import (
    # New and Corrected Functions for the Stable Strategy
    extract_resume_sections_safely,
    generate_final_detailed_report,
    fix_resume_issue,
    calculate_new_score,
    get_field_suggestions,
    generate_smart_resume_from_keywords,
    generate_full_ai_resume_html, # <--- YE LINE ADD KAREIN

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
        suggestion = data.get("suggestion")
        full_text = data.get("full_text")
        current_score = data.get("current_score") # <<< हमें JS से वर्तमान स्कोर चाहिए

        if not all([suggestion, full_text, isinstance(current_score, int)]):
            return jsonify({"success": False, "error": "Missing suggestion, text, or current_score."}), 400

        # STEP 1: Get the targeted fix from the AI.
        fix_result = generate_targeted_fix(suggestion, full_text) # <<< बदला हुआ
        
        if 'error' in fix_result:
            return jsonify({"success": False, "error": fix_result["error"]}), 500

        # STEP 2: Calculate the new score predictably.
        new_score = calculate_new_score(current_score, suggestion) # <<< बदला हुआ
        
        # Combine results into the final response
        final_response = {
            "section": fix_result["section"],
            "fixedContent": fix_result["fixedContent"],
            "newScore": new_score
        }

        # NOTE: We no longer return 'updatedAnalysis'. The frontend will handle the UI state.
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
        
@app.route('/generate-cover-letter-from-data', methods=['POST'])
def generate_cover_letter_from_data():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Invalid JSON data provided"}), 400

        extracted_data = data.get('extracted_data')
        job_title = data.get('job_title')
        company_name = data.get('company_name')

        if not extracted_data or not job_title or not company_name:
            return jsonify({"error": "extracted_data, job_title, and company_name are required"}), 400

        # extracted_data (JSON) se ek plain text resume banayein
        resume_text_parts = []
        if extracted_data.get('name'):
            resume_text_parts.append(f"Name: {extracted_data.get('name')}")
        if extracted_data.get('summary'):
            resume_text_parts.append(f"Summary: {extracted_data.get('summary')}")
        if extracted_data.get('work_experience'):
            resume_text_parts.append("\nWork Experience:")
            for exp in extracted_data.get('work_experience', []):
                resume_text_parts.append(f"- {exp.get('title')} at {exp.get('company')}")
        if extracted_data.get('skills'):
            skills_str = ', '.join(extracted_data.get('skills', []))
            resume_text_parts.append(f"\nSkills: {skills_str}")
        
        resume_text = "\n".join(resume_text_parts)

        if not resume_text.strip():
            return jsonify({"error": "Could not construct resume text from provided data"}), 400

        prompt = f"""
You are a professional cover letter writer. Write a concise cover letter (300-400 words) for the following details:
Job Title: {job_title}
Company Name: {company_name}
Resume Highlights: {resume_text[:6000]}
Include a greeting, an introduction, a body highlighting relevant skills and experiences, and a closing statement.
        """

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
            logger.error(f"Error in OpenAI API call for /generate-cover-letter-from-data: {str(e)}")
            return jsonify({"error": f"Failed to generate cover letter: {str(e)}"}), 500

    except Exception as e:
        logger.error(f"Error in /generate-cover-letter-from-data: {str(e)}")
        return jsonify({"error": "An unexpected server error occurred"}), 500
    
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

# Yeh function app.py me replace karein

@app.route("/generate-ai-resume", methods=["POST"])
def generate_ai_resume():
    try:
        data = request.get_json()
        contact_fields = ["name", "email", "phone", "location", "linkedin", "jobTitle"]
        user_info = {key: data.get(key, "") for key in contact_fields}
        section_data = {key: value for key, value in data.items() if key not in contact_fields}

        # generate_smart_resume_from_keywords function call pehle se hi sahi hai
        smart_content = generate_smart_resume_from_keywords(section_data)

        # <--- YAHAN CHANGE HAI --->
        # Ab hum generate_full_ai_resume_html ko sahi arguments ke saath call kar rahe hain.
        html = generate_full_ai_resume_html(user_info, smart_content)
        # <--- CHANGE YAHAN KHATAM HOTA HAI --->

        return jsonify({"success": True, "html": html})
    except Exception as e:
        import traceback
        traceback.print_exc() # Debugging ke liye traceback print karein
        return jsonify({"error": f"❌ Exception in generate-ai-resume: {type(e).__name__} - {str(e)}"}), 500

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

        text = extract_text_from_resume(file)
        if not text:
            return jsonify({"success": False, "error": "Could not extract text from the resume."}), 500

        # Step 1: Extract sections
        extracted_sections = extract_resume_sections_safely(text)
        if not extracted_sections or extracted_sections.get("error"):
            error_message = extracted_sections.get("error", "Failed to parse resume sections.")
            logger.error(f"Error from extract_resume_sections_safely: {error_message}")
            return jsonify({"success": False, "error": error_message}), 500

        # Step 2: Generate detailed ATS report
        ats_result = generate_final_detailed_report(text, extracted_sections)
        if not ats_result or ats_result.get("error"):
            error_message = ats_result.get("error", "Failed to generate ATS report.")
            logger.error(f"Error from generate_final_detailed_report: {error_message}")
            return jsonify({"success": False, "error": error_message}), 500
        
        # Step 3: Get field-aware suggestions
        field_info = get_field_suggestions(extracted_sections, text)
        if not field_info or field_info.get("error"):
            logger.warning("Could not get field suggestions, proceeding with default.")
            field_info = {"field": "General", "suggestions": []}

        # Step 4: Prepare the final data structure
        formatted_data = {
            "analysis": ats_result,
            "extracted_data": extracted_sections,
            "field_info": field_info
        }

        # Step 5: Calculate the score based on the detailed report
        fail_count = sum(1 for check in formatted_data['analysis'].values() if isinstance(check, dict) and check.get('status') in ['fail', 'improve'])
        formatted_data['score'] = max(40, 100 - (fail_count * 10))
        
        return jsonify({"success": True, "data": formatted_data})

    except Exception as e:
        import traceback
        logger.error(f"Error in /api/v1/analyze-resume: {traceback.format_exc()}")
        return jsonify({"success": False, "error": "An unexpected server error occurred."}), 500
from docx import Document
import html2text

@app.route('/api/v1/fix-issue-v2', methods=['POST'])
def handle_fix_issue_v2():
    try:
        if 'payload' not in request.form:
            logger.error("Payload missing in request for /api/v1/fix-issue-v2")
            return jsonify({"success": False, "error": "Missing payload"}), 400

        payload = json.loads(request.form.get('payload'))
        issue_text = payload.get('issue_text')
        extracted_data = payload.get('extracted_data')
        current_score = payload.get('current_score', 60) # Get current score, with a default

        if not issue_text or not extracted_data:
            logger.error("Missing 'issue_text' or 'extracted_data' in payload.")
            return jsonify({"success": False, "error": "Missing required data in payload"}), 400

        # Naye, reliable function ko call karein
        logger.info(f"Calling fix_resume_issue for: {issue_text[:80]}")
        # Make sure you have imported fix_resume_issue at the top of app.py
        fix_result = fix_resume_issue(issue_text, extracted_data) 

        if 'error' in fix_result:
            logger.error(f"fix_resume_issue function failed: {fix_result['error']}")
            return jsonify({"success": False, "error": fix_result['error']}), 500
        
        # Ek simple score badhane ka logic
        new_score = min(100, int(current_score) + 8)

        # Frontend ke liye response tayyar karein
        response_data = {
            "fix": fix_result,
            "new_score": new_score
        }

        return jsonify({"success": True, "data": response_data})

    except Exception as e:
        import traceback
        logger.error(f"Critical error in /api/v1/fix-issue-v2: {traceback.format_exc()}")
        return jsonify({"success": False, "error": "An unexpected server error occurred."}), 500

@app.route('/api/v1/generate-docx', methods=['POST'])
def generate_docx_from_html():
    try:
        # Check if the payload is in the request form
        if 'payload' not in request.form:
            logger.error("Payload missing in /api/v1/generate-docx request.")
            return jsonify({"success": False, "error": "Missing payload"}), 400
            
        data = json.loads(request.form.get('payload'))
        html_content = data.get("html_content")

        if not html_content:
            return jsonify({"success": False, "error": "No HTML content provided"}), 400

        # --- YEH HAI FINAL AUR SABSE RELIABLE TareeKA ---
        
        soup = BeautifulSoup(html_content, 'html.parser')
        doc = Document()

        # Helper function to add text safely
        def add_text(text):
            if text and text.strip():
                return text.strip()
            return ""

        # --- Document ko banana shuru karein ---

        # 1. Naam aur Title
        name_el = soup.find(id='preview-name')
        title_el = soup.find(id='preview-title')
        if name_el:
            doc.add_heading(add_text(name_el.text), level=0)
        if title_el:
            doc.add_paragraph(add_text(title_el.text))
        
        doc.add_paragraph() # Ek line ka space

        # 2. Sabhi sections ko process karein
        all_sections = soup.find_all('div', class_='preview-section')
        for section in all_sections:
            title_span = section.find('h3').find('span')
            if title_span:
                doc.add_heading(add_text(title_span.text).upper(), level=2)
            
            content_div = section.find('div', class_='content-container')
            if content_div:
                list_items = content_div.find_all('li')
                if list_items:
                    for li in list_items:
                        # Complex list items (Work Experience, Education)
                        h4 = li.find('h4')
                        p_sub = li.find('p')
                        details_ul = li.find('ul')
                        
                        if h4: # Agar item ke andar heading hai
                            p = doc.add_paragraph()
                            p.add_run(add_text(h4.text)).bold = True
                            if p_sub:
                                p.add_run(f'\n{add_text(p_sub.text)}').italic = True
                            if details_ul:
                               for detail_li in details_ul.find_all('li'):
                                   # Bullet point khud banayein
                                   bullet_p = doc.add_paragraph(f"• {add_text(detail_li.text)}")
                                   bullet_p.paragraph_format.left_indent = Inches(0.25)
                        else: # Simple list items (Skills, Languages)
                            # Bullet point khud banayein
                            bullet_p = doc.add_paragraph(f"• {add_text(li.text)}")
                            bullet_p.paragraph_format.left_indent = Inches(0.25)
                else:
                    # Agar list nahi hai to plain text
                    doc.add_paragraph(add_text(content_div.text))
            
            doc.add_paragraph() # Sections ke beech mein space

        # File ko memory mein save karein aur validate karein
        file_stream = io.BytesIO()
        doc.save(file_stream)
        file_stream.seek(0)

        # Temporary file ke liye test (debugging ke liye)
        temp_path = '/tmp/test_resume.docx'  # Use /tmp for Render compatibility
        with open(temp_path, 'wb') as f:
            f.write(file_stream.getvalue())
        # Verify if the file is valid (optional: you can remove this after testing)
        test_doc = Document(temp_path)
        if not test_doc:
            logger.error("Generated DOCX file is invalid during validation.")
            return jsonify({"success": False, "error": "Generated file is corrupted"}), 500
        os.remove(temp_path)  # Clean up temporary file
        file_stream.seek(0)  # Reset stream for sending

        return send_file(
            file_stream,
            as_attachment=True,
            download_name='ResumeFixerPro_Resume.docx',
            mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        )

    except Exception as e:
        logger.error(f"Error in /api/v1/generate-docx: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": "Failed to generate DOCX file"}), 500

# app.py

# ... (rest of the code above) ...

# Function to download PDF/DOCX from backend
@app.route('/download-generated-resume', methods=['POST'])
def download_generated_resume():
    try:
        data = request.get_json()
        html_content = data.get("html_content")
        file_format = data.get("format", "pdf")
        # Ensure 'name' is retrieved for filename, default if not found
        user_name_for_filename = data.get("name", "Generated_Resume") 

        if not html_content:
            return jsonify({"error": "No HTML content provided"}), 400

        # --- LATEST CSS FROM ai-resume-generator (8).css PASTE KIYA GAYA HAI ---
        # This CSS is directly copied from your frontend CSS for comprehensive styling in PDF/DOCX.
        # This is CRUCIAL for PDF rendering.
        css_for_pdf_and_docx = """
/* AI Resume Generator Specific Styles */
body { font-family: 'Inter', sans-serif; background-color: #f7f9fc; color: #334155; }
.card { background: white; border-radius: 0.5rem; box-shadow: 0 4px 12px rgba(0,0,0,0.06); padding: 1.5rem; margin-bottom: 1.5rem; }
.btn-primary { background-color: #1976D2; color: white; }
.btn-primary:hover { background-color: #1565C0; }
.btn-secondary { background-color: #6c757d; color: white; }
.btn-secondary:hover { background-color: #5a6268; }
button:disabled { background: #9ca3af; cursor: not-allowed; }
.field-label { display: flex; justify-content: space-between; align-items: center; width: 100%; }
.tag { font-size: 0.7rem; padding: 2px 6px; border-radius: 10px; font-weight: 500; }
.required { background-color: #fee2e2; color: #b91c1c; }
.optional { background-color: #e0e7ff; color: #4338ca; }
.form-step { display: none; animation: fadeIn 0.5s; }
.form-step.active { display: block; }
@keyframes fadeIn { from { opacity: 0; transform: translateY(20px); } to { opacity: 1; transform: translateY(0); } }
.progress-bar { display: flex; justify-content: space-between; margin-bottom: 1.5rem; }
.progress-step { text-align: center; flex: 1; padding-bottom: 10px; border-bottom: 4px solid #e5e7eb; color: #6b7280; position: relative; font-size: 0.8rem; }
.progress-step.active { border-bottom-color: #1976D2; color: #1976D2; font-weight: 600; }
.progress-step .step-number { width: 30px; height: 30px; border-radius: 50%; background-color: #e5e7eb; color: #6b7280; display: flex; align-items: center; justify-content: center; margin: 0 auto 8px auto; font-size: 0.8rem; }
.progress-step.active .step-number { background-color: #1976D2; color: white; }
#resume-preview-wrapper { background: white; padding: 0; width: 100%; border: 1px solid #ddd; }

/* ======== RESUME SPECIFIC STYLES - MODIFIED ======== */

/* Overall Resume Container */
.resume-container {
    font-family: 'Roboto', sans-serif;
    color: #333;
    line-height: 1.4;
    font-size: 9.5pt;
    background: #fff;
    max-width: 900px;
    margin: 0 auto; /* Remove auto margins to fit A4 */
    display: flex;
    box-shadow: 0 0 10px rgba(0, 0, 0, 0.1);
    border: 1px solid #eee;
    overflow: hidden;
    -webkit-print-color-adjust: exact; /* Ensure colors print correctly */
    print-color-adjust: exact;
    width: 210mm; /* A4 width in mm */
    min-height: 297mm; /* A4 height in mm */
}

.resume-container .content-wrapper {
    display: flex;
    flex-direction: row;
    width: 100%;
    flex-grow: 1;
}

/* Main Content Area (Right side) */
.resume-container .main-content {
    flex: 3;
    padding: 25px;
    box-sizing: border-box;
}

/* Sidebar Area (Left side) */
.resume-container .resume-sidebar {
    flex: 1;
    background-color: #f5f5f5;
    padding: 25px;
    color: #555;
    border-right: 1px solid #eee;
    box-sizing: border-box;
    min-width: 200px;
    max-width: 250px;
}

/* Name and Title Header */
.resume-container .name-title-header {
    text-align: left;
    margin-bottom: 20px;
    padding-bottom: 8px;
    border-bottom: 2px solid #1976D2;
}
.resume-container .name-title-header h1 {
    font-size: 28pt;
    font-weight: 700;
    color: #333;
    margin: 0;
    line-height: 1.1;
}
.resume-container .name-title-header .job-title {
    font-size: 12pt;
    color: #666;
    margin-top: 5px;
    text-transform: uppercase;
    letter-spacing: 0.8px;
}

/* Contact Info in Sidebar */
.resume-container .contact-info-sidebar {
    padding-top: 0;
    padding-bottom: 15px;
    margin-top: 0;
}
.resume-container .contact-info-sidebar h3 {
    font-size: 10pt;
    font-weight: 700;
    color: #333;
    border-bottom: 1px solid #ccc;
    padding-bottom: 5px;
    margin-bottom: 10px;
    text-transform: uppercase;
    margin-top: 0;
}
.resume-container .contact-info-sidebar p {
    font-size: 9.5pt;
    margin-bottom: 6px;
    display: flex;
    align-items: center;
    line-height: 1.2;
    word-break: break-word;
}
.resume-container .contact-info-sidebar p i {
    margin-right: 8px;
    color: #1976D2;
    font-size: 9.5pt;
    width: 15px;
    text-align: center;
}

/* General Resume Sections (both main content and sidebar) */
.resume-container .resume-section {
    margin-bottom: 25px;
    position: relative;
}
.resume-container .resume-section:last-child {
    margin-bottom: 0;
}
.resume-container .resume-section h2 {
    font-size: 11.5pt;
    font-weight: 700;
    color: #1976D2;
    border-bottom: 1px solid #ddd;
    padding-bottom: 3px;
    margin-bottom: 10px;
    margin-top: 25px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}
.resume-container .main-content .resume-section:first-child h2 {
    margin-top: 0;
    padding-top: 0;
}

/* Editable Content Styles */
.resume-container [contenteditable="true"] {
    outline: none;
}
.resume-container [contenteditable="true"]:focus {
    border: 1px dashed #4299e1; /* Blue dashed border on focus */
    border-radius: 2px;
    padding: 2px;
}
.resume-container ul li {
    font-size: 9.5pt;
    margin-bottom: 4px;
    position: relative;
    padding-left: 15px;
    line-height: 1.3;
}
.resume-container ul li::before {
    content: '•';
    position: absolute;
    left: 0;
    color: #1976D2;
    font-size: 10pt;
    line-height: 1;
    top: 0;
}
.resume-container strong {
    font-weight: 700;
}

/* Add Section Button */
.add-section-btn {
    background-color: #1976D2;
    color: white;
    border: none;
    padding: 5px 10px;
    border-radius: 0.25rem;
    cursor: pointer;
    margin-top: 10px;
}
.add-section-btn:hover {
    background-color: #1565C0;
}

/* Remove Section Button */
.remove-section-btn {
    position: absolute;
    top: 5px;
    right: 5px;
    background-color: #dc3545;
    color: white;
    border: none;
    padding: 2px 6px;
    border-radius: 50%;
    cursor: pointer;
    font-size: 12px;
    line-height: 1;
}
.remove-section-btn:hover {
    background-color: #c82333;
}

/* List items in sidebar (Skills, Languages, Certifications) */
.resume-container .resume-sidebar .resume-section ul {
    list-style: none;
    padding: 0;
    margin: 0;
}
.resume-container .resume-sidebar .resume-section ul li {
    font-size: 9.5pt;
    margin-bottom: 4px;
    position: relative;
    padding-left: 15px;
    line-height: 1.3;
}

/* Bullet points for main content (Work Experience, Projects, Education Details if bulleted) */
.resume-container .main-content ul {
    list-style: none;
    padding: 0;
    margin: 0;
}
.resume-container .main-content ul li {
    font-size: 9.5pt;
    margin-bottom: 4px;
    position: relative;
    padding-left: 15px;
    line-height: 1.3;
}

/* Styling for complex section items (e.g., Work Experience, Education, Projects) */
.resume-container .main-content .experience-item {
    margin-bottom: 20px;
}
.resume-container .main-content .experience-item:last-child {
    margin-bottom: 0;
}

.resume-container .main-content .experience-item .item-header {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    margin-bottom: 2px;
}
.resume-container .main-content .experience-item h4 {
    font-size: 10pt;
    margin: 0;
    color: #333;
    font-weight: 600;
    flex-grow: 1;
    line-height: 1.2;
}
.resume-container .main-content .experience-item .item-meta {
    font-size: 9.5pt;
    color: #666;
    font-weight: normal;
    margin: 0;
    text-align: right;
    white-space: nowrap;
    padding-left: 10px;
    line-height: 1.2;
}
.resume-container .main-content .experience-item .item-meta span:first-child {
    font-weight: 500;
}
.resume-container .main-content .experience-item .item-meta .duration {
    margin-left: 5px;
}
/* For project descriptions or education details that are paragraphs */
.resume-container .main-content .experience-item .item-description {
    font-size: 9.5pt;
    line-height: 1.4;
    margin-bottom: 5px;
    margin-top: 5px;
}


/* Styling for Profile Summary & other direct paragraphs */
.resume-container .resume-section p {
    font-size: 9.5pt;
    line-height: 1.5;
    margin-bottom: 10px;
    margin-top: 0;
}


/* Responsive adjustments */
@media (max-width: 768px) {
    .resume-container .content-wrapper {
        flex-direction: column;
    }
    .resume-container .resume-sidebar {
        border-right: none;
        border-bottom: 1px solid #eee;
        min-width: unset;
        max-width: 100%;
    }
    .resume-container {
        margin: 0;
        max-width: 100%;
        box-shadow: none;
        border: none;
        width: 100%;
    }
    .resume-container .main-content {
        padding: 15px;
    }
    .resume-container .resume-sidebar {
        padding: 15px;
    }
}

/* Ensure no page breaks inside sections */
.resume-container .resume-section {
    page-break-inside: avoid;
}
        """

        full_html = f"""
        <html>
            <head>
                <meta charset="UTF-8">
                <style>
                    {css_for_pdf_and_docx}
                </style>
            </head>
            <body>{html_content}</body>
        </html>
        """

        if file_format == 'pdf':
            # Use pdfkit (wkhtmltopdf) for PDF generation
            options = {
                'page-size': 'A4',
                'margin-top': '0.7in',
                'margin-right': '0.7in',
                'margin-bottom': '0.7in',
                'margin-left': '0.7in',
                'encoding': "UTF-8",
                'enable-local-file-access': None # Important for any local assets, though not directly used here.
            }
            pdf_file = pdfkit.from_string(full_html, False, options=options)
            return send_file(io.BytesIO(pdf_file), as_attachment=True, download_name=f'{user_name_for_filename}.pdf', mimetype='application/pdf')

        elif file_format == 'docx':
            # Check if html2docx is properly imported or available (it was set to None if import failed)
            if html2docx is None:
                 return jsonify({"error": "DOCX conversion library (html2docx) not available on server."}), 500
            
            # DOCX: Pass full_html and a title (filename)
            # This fixes the TypeError: html2docx() missing 1 required positional argument: 'title'
            docx_bytes = html2docx(full_html, title=f'{user_name_for_filename}.docx') 
            return send_file(io.BytesIO(docx_bytes), as_attachment=True, download_name=f'{user_name_for_filename}.docx', mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document')
            
        else:
            return jsonify({"error": "Unsupported format"}), 400

    except Exception as e:
        print(f"Error in /download-generated-resume: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": "Failed to generate file on server."}), 500
    
if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"Starting Flask app on port {port}")
    app.run(host="0.0.0.0", port=port)

logger.info("Flask app initialization complete.")
