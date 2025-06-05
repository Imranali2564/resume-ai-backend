import logging
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from werkzeug.utils import secure_filename
import os
import uuid
import json
import re
import base64
import io
import tempfile
from resume_ai_analyzer import (
    generate_resume_summary,
    generate_michelle_template_html,
    extract_text_from_resume,
    extract_resume_sections,
    analyze_resume_with_openai,
    extract_text_from_pdf,
    extract_text_from_docx,
    check_ats_compatibility,
    extract_keywords_from_jd,
    compare_resume_with_keywords,
    analyze_job_description,
    fix_resume_formatting,
    generate_section_content,
    generate_ats_report,
    calculate_resume_score
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
    import fitz
    import PyPDF2
    import pdfkit
except ImportError as e:
    logging.error(f"Failed to import required dependencies: {str(e)}")
    raise

logging_level = logging.INFO if os.environ.get("FLASK_ENV") != "development" else logging.DEBUG
logging.basicConfig(level=logging_level)
logger = logging.getLogger(__name__)
logging.getLogger("pdfminer").setLevel(logging.ERROR)

logger.info("Starting Flask app initialization...")

app = Flask(__name__, static_url_path='/static')
CORS(app, resources={r"/*": {"origins": ["https://resumefixerpro.com", "http://localhost:3000"]}})

UPLOAD_FOLDER = '/tmp/Uploads'
STATIC_FOLDER = '/tmp/static'
try:
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    os.makedirs(STATIC_FOLDER, exist_ok=True)
    logger.info(f"Directories created: {UPLOAD_FOLDER}, {STATIC_FOLDER}")
except Exception as e:
    logger.error(f"Failed to create directories: {str(e)}")
    raise RuntimeError(f"Failed to create directories: {str(e)}")
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['STATIC_FOLDER'] = STATIC_FOLDER

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
        if not file or file.filename == '':
            return jsonify({"error": "No file uploaded"}), 400

        text = extract_text_from_resume(file)
        if not text.strip():
            return jsonify({"error": "Failed to extract resume text"}), 500

        cleaned_text = remove_unnecessary_personal_info(text)
        parsed_sections = extract_resume_sections(cleaned_text)
        if not parsed_sections:
            return jsonify({"error": "Failed to parse resume sections"}), 500

        # Ensure all expected sections are present
        expected_sections = [
            'name', 'job_title', 'phone', 'email', 'location', 'website',
            'summary', 'education', 'skills', 'work_experience', 'projects',
            'certifications', 'languages', 'achievements', 'hobbies',
            'internships', 'publications', 'volunteer_experience', 'references'
        ]
        for section in expected_sections:
            parsed_sections[section] = parsed_sections.get(section, '')

        # Generate summary if missing
        name = parsed_sections.get("name", "")
        role = parsed_sections.get("job_title", "")
        experience = parsed_sections.get("work_experience", "")
        skills = parsed_sections.get("skills", "")
        if not parsed_sections.get("summary"):
            summary = generate_resume_summary(name, role, experience, skills)
            parsed_sections["summary"] = summary if isinstance(summary, str) else ''

        # Generate ATS report and score
        ats_report = generate_ats_report(cleaned_text)
        score = calculate_resume_score(parsed_sections["summary"], ats_report.get("issues", []))

        return jsonify({
            "resume_text": cleaned_text,
            "parsedResumeContent": parsed_sections,
            "suggestions": [],  # Suggestions handled by frontend
            "ats_report": ats_report,
            "score": score
        })
    except Exception as e:
        logger.error(f"Error in /upload: {str(e)}")
        return jsonify({"error": f"Failed to process resume: {str(e)}"}), 500

@app.route('/ats-check', methods=['POST'])
def check_ats():
    filepath = None
    try:
        file = request.files.get('file') or request.files.get('resume')
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

        ats_result = check_ats_compatibility(filepath)
        if "error" in ats_result:
            return jsonify({"error": ats_result["error"]}), 500

        return jsonify({
            "issues": ats_result.get("issues", []),
            "score": ats_result.get("score", 70)
        })
    except Exception as e:
        logger.error(f"Error in /ats-check: {str(e)}")
        return jsonify({"error": f"Failed to check ATS compatibility: {str(e)}"}), 500
    finally:
        if filepath:
            cleanup_file(filepath)

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
        data = request.get_json()
        suggestion = data.get("suggestion")
        full_text = data.get("full_text")

        if not suggestion or not full_text:
            logger.error("Missing suggestion or full_text in /fix-suggestion request")
            return jsonify({"error": "Missing suggestion or full text"}), 400

        result = generate_section_content(suggestion, full_text)
        if 'error' in result:
            logger.error(f"Error in generate_section_content: {result['error']}")
            return jsonify({"error": result["error"]}), 400

        if not isinstance(result, dict) or "section" not in result or "fixedContent" not in result:
            logger.error(f"Invalid response format from generate_section_content: {result}")
            return jsonify({"error": "Invalid response format from AI"}), 500

        logger.info(f"Successfully generated content for section: {result['section']}")
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error in /fix-suggestion: {str(e)}")
        return jsonify({"error": f"Failed to process suggestion: {str(e)}"}), 500

@app.route('/preview-resume', methods=['POST'])
def preview_resume():
    try:
        data = request.get_json()
        sections = data.get('sections')
        if not sections:
            logger.error("No sections provided in /preview-resume request")
            return jsonify({"error": "No sections provided"}), 400

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

        temp_file = None
        if file_format == "pdf":
            temp_file = os.path.join(app.config['STATIC_FOLDER'], f"resume_{uuid.uuid4()}.pdf")
            pdfkit.from_string(wrapped_html, temp_file)
            file_data = open(temp_file, 'rb').read()
        else:
            temp_file = os.path.join(app.config['STATIC_FOLDER'], f"resume_{uuid.uuid4()}.docx")
            doc = Document()
            for para in html_content.split('<br>'):
                doc.add_paragraph(para.strip())
            doc.save(temp_file)
            file_data = open(temp_file, 'rb').read()

        return jsonify({
            "data": base64.b64encode(file_data).decode('utf-8')
        })
    except Exception as e:
        logger.error(f"Error in /final-resume: {str(e)}")
        return jsonify({"error": f"Failed to generate final resume: {str(e)}"}), 500
    finally:
        if temp_file:
            cleanup_file(temp_file)

@app.route('/generate-cover-letter', methods=['POST'])
def generate_cover_letter():
    filepath = None
    try:
        file = request.files.get('file') or request.files.get('resume')
        job_title = request.form.get('job_title')
        company_name = request.form.get('company_name')

        if not file or not job_title or not company_name:
            return jsonify({"error": "File, job title, and company name are required"}), 400

        resume_text = extract_text_from_resume(file)
        if not resume_text.strip():
            return jsonify({"error": "Could not extract text from resume"}), 400

        from openai import OpenAI
        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        prompt = f"""
You are a professional cover letter writer. Write a concise cover letter (300-400 words) for:
- Job Title: {job_title}
- Company Name: {company_name}
- Resume: {resume_text[:6000]}
Include a greeting, an introduction, a body highlighting relevant skills and experiences, and a closing statement.
"""
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}]
        )
        cover_letter = response.choices[0].message.content.strip()
        return jsonify({"cover_letter": cover_letter})
    except Exception as e:
        logger.error(f"Error in /generate-cover-letter: {str(e)}")
        return jsonify({"error": f"Failed to generate cover letter: {str(e)}"}), 500

@app.route('/download-cover-letter', methods=['POST'])
def download_cover_letter():
    output_path = None
    try:
        data = request.get_json()
        cover_letter = data.get('cover_letter')
        if not cover_letter:
            return jsonify({"error": "No cover letter provided"}), 400

        output_path = os.path.join(app.config['STATIC_FOLDER'], f"cover_letter_{uuid.uuid4()}.docx")
        doc = Document()
        doc.add_heading("Cover Letter", level=1)
        for line in cover_letter.splitlines():
            line = line.strip()
            if line:
                doc.add_paragraph(line)
        doc.save(output_path)
        file_data = open(output_path, 'rb').read()
        return jsonify({
            "data": base64.b64encode(file_data).decode('utf-8')
        })
    except Exception as e:
        logger.error(f"Error in /download-cover-letter: {str(e)}")
        return jsonify({"error": f"Failed to download cover letter: {str(e)}"}), 500
    finally:
        if output_path:
            cleanup_file(output_path)

@app.route('/resume-score', methods=['POST'])
def resume_score():
    filepath = None
    try:
        if request.is_json:
            data = request.get_json()
            resume_text = data.get("resume_text", "")
        else:
            file = request.files.get('file')
            if not file:
                return jsonify({"error": "No file uploaded"}), 400
            filename = secure_filename(file.filename)
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)
            ext = os.path.splitext(filepath)[1].lower()
            resume_text = extract_text_from_pdf(filepath) if ext == ".pdf" else extract_text_from_docx(filepath)

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
Just return a number between 0 and 100.
"""
        from openai import OpenAI
        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "system", "content": "You are a strict but fair resume scoring assistant."}, {"role": "user", "content": prompt}]
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
    try:
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
    except Exception as e:
        logger.error(f"Error in /optimize-keywords: {str(e)}")
        return jsonify({"error": f"Failed to optimize keywords: {str(e)}"}), 500

@app.route('/generate-ai-resume', methods=['POST'])
def generate_ai_resume():
    try:
        data = request.get_json()
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

        def generate_section_content(section_name, user_input):
            if not user_input.strip():
                return ""
            prompts = {
                "summary": f"""
You are a resume writing assistant. Based on:
Education: {education}
Experience: {experience}
Skills: {skills}
Write a 2-3 line professional summary.
""",
                "education": f"""
You are a resume writing assistant. Education details: '{user_input}'.
Generate a professional education entry. Include degree, institution, and years (e.g., 2020-2024). Make assumptions if needed.
Format as plain text, e.g.:
B.Tech in Computer Science, XYZ University, 2020-2024
""",
                "experience": f"""
You are a resume writing assistant. Experience details: '{user_input}'.
Generate a professional experience entry. Include job title, company, duration (e.g., June 2023 - August 2023), and 1-2 lines of responsibilities.
Format as plain text, e.g.:
Software Intern, ABC Corp, June 2023 - August 2023
Developed web applications using React and Node.js
""",
                "skills": f"""
You are a resume writing assistant. Skills: '{user_input}'.
Generate a professional skills section. Add 2-3 relevant skills if possible, format as bullet list.
Format as plain text, e.g.:
- Python
- JavaScript
- SQL
""",
                "certifications": f"""
You are a resume writing assistant. Certifications: '{user_input}'.
Generate a professional certifications section. Include name, organization, and year (e.g., 2023). Make assumptions if needed.
Format as plain text, e.g.:
Certified Python Developer, XYZ Institute, 2023
""",
                "languages": f"""
You are a resume writing assistant. Languages: '{user_input}'.
Generate a professional languages section. Include proficiency levels (e.g., Fluent), format as list.
Format as plain text, e.g.:
English (Fluent)
Spanish (Intermediate)
""",
                "hobbies": f"""
You are a resume writing assistant. Hobbies: '{user_input}'.
Generate a professional hobbies section. Add 1-2 related hobbies if possible, format as bullet list.
Format as plain text, e.g.:
- Reading
- Hiking
"""
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
                return res.choices[0].message.content.strip()
            except Exception as e:
                logger.error(f"Error generating {section_name}: {str(e)}")
                return user_input

        if summary:
            summary = generate_section_content("summary", summary)
        else:
            summary = generate_section_content("summary", f"Education: {education}\nExperience: {experience}\nSkills: {skills}")

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
            return f"""
            <div class='section' style='margin-bottom: 1.2rem;'>
              <h3 style='font-size: 0.95rem; line-height: 1.3; color: #222; margin-bottom: 4px; border-bottom: 1px solid #ccc;'>- {title.upper()}</h3>
              <div>{html_content}</div>
            </div>
            """

        top = f"""
        <div style='text-align: center; margin-bottom: 1.2rem;'>
          <div style='font-size: 1.3rem; font-weight: bold; color: #1D75E5;'>{name}</div>
          <div style='font-size: 0.9rem; color: #333;'>{email} | {phone} | {location}</div>
        </div>
        """
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
        return jsonify({"analysis": result})
    except Exception as e:
        logger.error(f"Error in /analyze-jd: {str(e)}")
        return jsonify({"error": f"Failed to analyze job description: {str(e)}"}), 500

@app.route('/convert-format', methods=['POST'])
def convert_format():
    upload_path = None
    output_path = None
    html_temp_path = None
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
        os.chmod(upload_path, 0o644)
        file_size = os.path.getsize(upload_path) / 1024
        if file_size == 0:
            logger.error(f"Empty file uploaded: {filename}")
            return jsonify({"error": "Uploaded file is empty"}), 400

        if ext == '.pdf':
            with open(upload_path, 'rb') as f:
                pdf_reader = PyPDF2.PdfReader(f)
                if pdf_reader.is_encrypted:
                    logger.error("Uploaded PDF is encrypted")
                    return jsonify({"error": "PDF is encrypted and cannot be processed."}), 400

        if ext == '.docx':
            try:
                doc = Document(upload_path)
            except Exception as e:
                logger.error(f"Invalid or corrupted DOCX file: {str(e)}")
                return jsonify({"error": f"Invalid or corrupted DOCX file: {str(e)}"}), 400

        if target_format == 'text':
            text = extract_text_from_pdf(upload_path) if ext == '.pdf' else extract_text_from_docx(upload_path)
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

        elif ext == '.pdf' and target_format == 'docx':
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
            return send_file(
                output_path,
                as_attachment=True,
                download_name="converted.docx",
                mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
            )

        else:
            logger.error(f"Unsupported conversion request: {ext} to {target_format}")
            return jsonify({"error": "Unsupported conversion request. Only PDF to DOCX, DOCX to PDF, or text extraction are supported."}), 400
    except Exception as e:
        logger.error(f"Error in /convert-format: {str(e)}")
        return jsonify({"error": f"Failed to process file: {str(e)}"}), 500
    finally:
        for path in [upload_path, output_path, html_temp_path]:
            if path:
                cleanup_file(path)

@app.route('/fix-formatting', methods=['POST'])
def fix_formatting():
    filepath = None
    try:
        file = request.files.get('file') or request.files.get('resume')
        if not file:
            return jsonify({"error": "No file uploaded"}), 400

        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        result = fix_resume_formatting(filepath)
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error in /fix-formatting: {str(e)}")
        return jsonify({"error": f"Failed to process resume formatting: {str(e)}"}), 500
    finally:
        if filepath:
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
    filepath = None
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
        receiver_email = sender_email
        smtp_server = "smtp.hostinger.com"
        smtp_port = os.environ.get("SMTP_PORT", 587)
        smtp_password = os.environ.get("SMTP_PASSWORD")

        msg = MIMEMultipart()
        msg['From'] = sender_email
        msg['To'] = receiver_email
        msg['Subject'] = 'Feedback Submission from ResumeFixerPro'

        body = f"""
Name: {name}
Email: {email}
Message:
{message}
"""
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

        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        server.login(sender_email, smtp_password)
        server.sendmail(sender_email, receiver_email, msg.as_string())
        server.quit()
        return jsonify({"success": True, "message": "Feedback sent successfully"})
    except Exception as e:
        logger.error(f"Error in /send-feedback: {str(e)}")
        return jsonify({"error": f"Failed to send feedback: {str(e)}"}), 500
    finally:
        if filepath:
            cleanup_file(filepath)

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
        receiver_email = sender_email
        smtp_server = "smtp.gmail.com"
        smtp_port = os.environ.get("587")
        smtp_password = os.environ.get("SMTP_PASSWORD")

        msg = MIMEMultipart()
        email = sender_email
        msg['To'] = smtp_email
        msg['From'] = receiver_email
        msg['Subject'] = 'Contact Message from ResumeFixerPro'

        body = f"""
New Contact Message:
Name: {name}
Email: {email}
Message:
{message}
"""
        msg.attach(MIMEText(body, 'plain'))

        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        server.logi(n)
        server.sen(dmsg)
        server.quit()

        return jsonify({"success": True, "message": "Message sent successfully!"})
    except Exception as e:
        logger.error(f"Error in /send-message: {str(e)}")
        return jsonify({"error": f"Failed to send message: {str(e)}"}), 500

@app.route('/extract-sections', methods=['POST'])
def extract_sections():
    try:
        data = request.get_json()
        text = data.get('text', '')
        if not text.strip():
            logger.warning("No resume text provided in /extract-sections request")
            return jsonify({"error": "No resume text provided"}), 400

        sections = extract_resume_sections(text)

        if not sections or not isinstance(sections, dict):
            logger.warning(f"Invalid section extracted: {sections}")
            return jsonify({"error": "Failed to extract sections: Resume format may be unsupported or text is too unstructured."}), 400

        if not any(sections.values()):
            logger.warning("No sections could be extracted from the resume text")
            return jsonify({"error": "Failed to extract sections: No recognizable sections found in the resume."}), 400

        logger.info(f"Successfully extracted sections: {list(sections.keys())}")
        return jsonify(sections)
    except Exception as e:
        logger.error(f"Error in /extract-sections: {str(e)}")
        return jsonify({"error": f"Failed to extract sections: {str(e)}"}), 500

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"Starting Flask app on port {port}")
    app.run(host="0.0.0.0", port=port)

logger.info("Flask app initialization complete.")
