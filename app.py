import logging
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from werkzeug.utils import secure_filename
import os
import uuid
import json
import re
from resume_ai_analyzer import generate_resume_summary, generate_michelle_template_html, extract_text_from_resume, extract_resume_sections
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
    from resume_ai_analyzer import (
        analyze_resume_with_openai,
        extract_text_from_pdf,
        extract_text_from_docx,        
        check_ats_compatibility,
        extract_keywords_from_jd,
        compare_resume_with_keywords,
        analyze_job_description,
        fix_resume_formatting,
        generate_section_content,
        generate_resume_summary,
        extract_resume_sections,
        generate_michelle_template_html,
    )
except ImportError as e:
    logging.error(f"Failed to import resume_ai_analyzer: {str(e)}")
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

@app.route('/upload', methods=['POST'])
def upload_resume():
    file = request.files.get('file') or request.files.get('resume')
    if not file or file.filename == '':
        logger.error("No file uploaded in request")
        return jsonify({'error': 'No file found'}), 400

    # Validate file extension
    ext = os.path.splitext(file.filename)[1].lower()
    allowed_extensions = {'.pdf', '.docx'}
    if ext not in allowed_extensions:
        logger.error(f"Unsupported file format: {ext}")
        return jsonify({'error': f'Unsupported file format: {ext}. Please upload a PDF or DOCX file.'}), 400

    # Validate file size
    file.seek(0, os.SEEK_END)
    file_size = file.tell() / 1024  # Size in KB
    file.seek(0)  # Reset file pointer
    if file_size == 0:
        logger.error(f"Uploaded file {file.filename} is empty")
        return jsonify({'error': 'Uploaded file is empty'}), 400
    if file_size > 10240:  # 10MB limit
        logger.error(f"File {file.filename} is too large: {file_size:.2f} KB")
        return jsonify({'error': f'File is too large: {file_size:.2f} KB. Maximum allowed size is 10MB.'}), 400
    logger.debug(f"File size: {file_size:.2f} KB")

    # Save the file
    filename = secure_filename(file.filename)
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    try:
        file.save(filepath)
        if not os.path.exists(filepath):
            logger.error(f"Failed to save file to {filepath}")
            return jsonify({'error': 'Failed to save file on server'}), 500

        # Set file permissions for Render compatibility
        os.chmod(filepath, 0o644)

        # Verify saved file size
        saved_size = os.path.getsize(filepath) / 1024
        if saved_size == 0:
            logger.error(f"Saved file {filepath} is empty")
            return jsonify({'error': 'Saved file is empty'}), 500

        # Extract text from the resume
        resume_text = extract_text_from_resume(file)
        if not resume_text:
            logger.error(f"Failed to extract text from {filepath}")
            return jsonify({'error': 'Failed to extract text from resume. The file might be unreadable or contain only images.'}), 500

        logger.info(f"Successfully processed file {filename}: {len(resume_text)} characters extracted")
        return jsonify({'resume_text': resume_text})

    except Exception as e:
        logger.error(f"Error in /upload: {str(e)}")
        return jsonify({'error': 'Failed to process file: {str(e)}'}), 500
    finally:
        cleanup_file(filepath)

@app.route('/ats-check', methods=['POST'])
def check_ats():
    file = request.files.get('file') or request.files.get('resume')
    if not file or file.filename == '':
        return jsonify({'error': 'No file selected for upload'}), 400
    ext = os.path.splitext(file.filename)[1].lower()
    allowed_extensions = {'.pdf', '.docx'}
    if ext not in allowed_extensions:
        return jsonify({'error': f'Unsupported file format: {ext}. Please upload a PDF or DOCX file.'}), 400
    filename = secure_filename(file.filename)
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    try:
        file.save(filepath)
        ats_result = check_ats_compatibility(filepath)
        if not ats_result or not isinstance(ats_result, dict) or not ats_result.get("issues"):
            resume_text = extract_text_from_pdf(filepath) if ext == ".pdf" else extract_text_from_docx(filepath)
            ats_issues = []
            if not re.search(r'[\w\.-]+@[\w-.-]+', resume_text):
                ats_issues.append("Missing email address - ATS systems often require contact information.")
            if len(resume_text.splitlines()) < 10:
                ats_issues.append("Resume content too short - ATS systems may not parse it effectively.")
            if not re.search(r'\b(?:[A-Z][a-z]+(?:,\s*)?)+\b', resume_text):
                ats_issues.append("Missing location - ATS systems often look for location details.")
            ats_result = {"issues": ats_issues, "score": max(0, 100 - len(ats_issues) * 20)}
        return jsonify(ats_result)
    except Exception as e:
        logger.error(f"Error in /ats-check: {str(e)}")
        return jsonify({'error': 'Failed to check ATS compatibility'}), 500
    finally:
        cleanup_file(filepath)

@app.route('/analyze', methods=['POST'])
def analyze_resume():
    try:
        data = request.get_json()
        resume_text = data.get('resume_text')
        if not resume_text or not isinstance(resume_text, str) or not resume_text.strip():
            return jsonify({'error': 'Invalid or empty resume text'}), 400
        result = analyze_resume_with_openai(resume_text, atsfix=False)
        if "error" in result:
            return jsonify({"error": "Unable to generate suggestions. Please check if the API key is set."})
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error in /analyze: {str(e)}")
        return jsonify({'error': 'Failed to analyze resume'}), 500

@app.route('/fix-suggestion', methods=['POST'])
def fix_suggestion():
    try:
        data = request.get_json()
        suggestion = data.get("suggestion")
        full_text = data.get("full_text")  # Adjusted to match frontend payload

        if not suggestion or not full_text:
            logger.error("Missing suggestion or full_text in /fix-suggestion request")
            return jsonify({"error": "Missing suggestion or full text"}), 400

        result = generate_section_content(suggestion, full_text)
        if 'error' in result:
            logger.error(f"Error in generate_section_content: {result['error']}")
            return jsonify({'error': result['error']}), 500

        # Validate the response
        if not isinstance(result, dict) or "section" not in result or "fixedContent" not in result:
            logger.error(f"Invalid response format from response in generate_section_content: {result}")
            return jsonify({'error': 'Invalid response format from AI'}), 500

        logger.info(f"Successfully generated content for section: {result['section']}")
        return jsonify(result)

    except Exception as e:
        logger.error(f"Error in /fix-suggestion: {str(e)}")
        return jsonify({"error": "Failed to process suggestion: {str(e)}"}), 500

@app.route('/preview-resume', methods=['POST'])
def preview_resume():
    try:
        data = request.get_json()
        sections = data.get('sections')
        if not sections:
            logger.error("No sections provided in /preview-resume request")
            return jsonify({'error': 'No sections provided'}), 400

        # Generate HTML with proper width and margin
        html_content = """
        <div style='width: 90%; margin: 0 auto; font-family: Arial, sans-serif;'>
        """
        for section, content in sections.items():
            section_title = section.replace('_', ' ').title()
            html_content += f"""
            <div class='section' style='margin-bottom: 1.2rem;'>
              <h3 style='font-size: 0.95rem; line-height: 1.3; color: #222; margin-bottom: 4px; border-bottom: 1px solid #ccc;'>{section_title.upper()}</h3>
              <div style='line-height: 1.5;'>{content.replace('\n', '<br>')}</div>
            </div>
            """
        html_content += "</div>"

        # Generate plain text for preview
        formatted_resume = []
        for section, content in sections.items():
            section_title = section.replace('_', ' ').title()
            formatted_resume.append(section_title.upper())
            lines = content.split('\n')
            for line in lines:
                line = line.strip()
                if line:
                    if not line.startswith('- '):
                        line = '- ' + line
                    formatted_resume.append(line)
            formatted_resume.append('')
        
        # Clean up excessive blank lines
        cleaned_resume = []
        last_was_empty = False
        for line in formatted_resume:
            if line == '' and last_was_empty:
                continue
            cleaned_resume.append(line)
            last_was_empty = (line == '')
        resume_text = '\n'.join(cleaned_resume).strip()

        return jsonify({'preview_text': resume_text, 'preview_html': html_content})
    except Exception as e:
        logger.error(f"Error in /preview-resume: {str(e)}")
        return jsonify({'error': f'Failed to generate preview: {str(e)}'}), 500

@app.route('/final-resume', methods=['POST'])
def final_resume_download():
    try:
        data = request.get_json()
        html_content = data.get("html")  # Frontend sends the HTML content directly
        file_format = data.get("format", "pdf")

        if not html_content:
            logger.error("No HTML content provided in /final-resume request")
            return jsonify({"error": "Invalid HTML content provided"}), 400

        # Wrap HTML content with proper width and margin for PDF/DOCX
        wrapped_html = f"""
        <div style='width: 90%; margin: 0 auto; font-family: Arial, sans-serif; padding: 20px;'>
            {html_content}
        </div>
        """

        if file_format in ["pdf", "docx"]:
            if file_format == "pdf":
                import pdfkit
                with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as pdf_file:
                    pdfkit.from_string(wrapped_html, pdf_file.name)
                    logger.info(f"Generated PDF at: {pdf_file.name}")
                    return send_file(
                        pdf_file.name,
                        as_attachment=True,
                        download_name="Final_Resume.pdf",
                        mimetype='application/pdf'
                    )

            else:  # For DOCX, use html2docx to convert the HTML to DOCX
                try:
                    from html2docx import html2docx
                except ImportError as e:
                    logger.error(f"html2docx not installed: {str(e)}")
                    return jsonify({'error': "Server error: html2docx not installed"}), 500

                docx_bytes = html2docx(wrapped_html)
                logger.info(f"Generated DOCX in memory")
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
        return jsonify({"error": str(e)}), 500

@app.route('/generate-cover-letter', methods=['POST'])
def generate_cover_letter():
    file = request.files.get('file') or request.files.get('resume')
    job_title = request.form.get('job_title')
    company_name = request.form.get('company_name')

    if not file or not job_title or not company_name:
        return jsonify({'error': 'File, job title, and company name are required'}), 400

    filename = secure_filename(file.filename)
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    try:
        file.save(filepath)
        resume_text = extract_text_from_resume(file)
        if not resume_text.strip():
            return jsonify({'error': 'Could not extract text from resume'}), 400

        prompt = f"""
You are a professional cover letter writer. Write a concise cover letter (300-400 words) for the following details:

Job Title: {job_title}
Company Name: {company_name}
Resume: {resume_text[:6000]}

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
            logger.error(f"Error in OpenAI API call for /generate-cover-letter: {str(e)}")
            return jsonify({'error': 'Failed to generate cover letter'}), 500
    except Exception as e:
        logger.error(f"Error in /generate-cover-letter: {str(e)}")
        return jsonify({'error': 'Failed to generate cover letter'}), 500
    finally:
        cleanup_file(filepath)

@app.route('/download-cover-letter', methods=['POST'])
def download_cover_letter():
    data = request.get_json()
    cover_letter = data.get('cover_letter')
    if not cover_letter:
        return jsonify({'error': 'No cover letter provided'}), 400
    output_path = os.path.join(app.config['UPLOAD_FOLDER'], f"cover_letter_{uuid.uuid4()}.docx")
    try:
        doc = Document()
        doc.add_heading("Cover Letter", level=1)
        for line in cover_letter.splitlines():
            line = line.strip()
            if line:
                doc.add_paragraph(line)
        doc.save(output_path)
        return send_file(output_path, as_attachment=True, download_name="Cover_Letter.docx")
    except Exception as e:
        logger.error(f"Error in /download-cover-letter: {str(e)}")
        return jsonify({'error': 'Failed to download cover letter'}), 500
    finally:
        cleanup_file(output_path)

@app.route('/resume-score', methods=['POST'])
def resume_score():
    file = request.files.get('file')
    if not file:
        return jsonify({'error': 'No file uploaded'}), 400
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(file.filename))
    try:
        file.save(filepath)
        ext = os.path.splitext(filepath)[1].lower()
        if ext == ".pdf":
            resume_text = extract_text_from_pdf(filepath)
        elif ext == ".docx":
            resume_text = extract_text_from_docx(filepath)
        else:
            return jsonify({'error': 'Unsupported file format'}), 400
        if not resume_text.strip():
            return jsonify({'error': 'No extractable text found in resume'}), 400
        prompt = f"""
You are a professional resume reviewer. Give a resume score between 0 and 100 based on:
- Formatting and readability
- Grammar and professionalism
- Use of action verbs and achievements
- Keyword optimization for ATS
- Overall impression and completeness

Resume:
{resume_text}

Just return a number between 0 and 100, nothing else.
    """
        try:
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
            logger.error(f"Error in OpenAI API call for /resume-score: {str(e)}")
            return jsonify({"score": 70})
    except Exception as e:
        logger.error(f"Error in /resume-score: {str(e)}")
        return jsonify({"score": 70})
    finally:
        cleanup_file(filepath)

@app.route('/optimize-keywords', methods=['POST'])
def optimize_keywords():
    resume_file = request.files.get('resume')
    job_description = request.form.get('job_description', '')

    if not resume_file or not job_description:
        return jsonify({'error': 'Missing resume or job description'}), 400

    resume_text = extract_text_from_resume(resume_file)
    if not resume_text.strip():
        return jsonify({'error': 'No extractable text found in resume'}), 400

    jd_keywords = extract_keywords_from_jd(job_description)
    if not jd_keywords:
        return jsonify({'error': 'No keywords extracted from job description'}), 400

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
                "summary": f"""
You are a resume writing assistant. Based on the following:
Education: {education}
Experience: {experience}
Skills: {skills}
Write a 2-3 line professional summary for a resume.
""",
                "education": f"""
You are a resume writing assistant. The user has provided the following education details: '{user_input}'.
Based on this, generate a professional education entry for a resume. Include degree, institution, and years (e.g., 2020-2024). If details are missing, make reasonable assumptions.
Format the output as plain text, e.g., 'B.Tech in Computer Science, XYZ University, 2020-2024'.
""",
                "experience": f"""
You are a resume writing assistant. The user has provided the following experience details: '{user_input}'.
Based on this, generate a professional experience entry for a resume. Include job title, company, duration (e.g., June 2023 - August 2023), and a brief description of responsibilities (1-2 lines).
Format the output as plain text, e.g., 'Software Intern, ABC Corp, June 2023 - August 2023, Developed web applications using React and Node.js'.
""",
                "skills": f"""
You are a resume writing assistant. The user has provided the following skills: '{user_input}'.
Based on this, generate a professional skills section for a resume. Expand the list by adding 2-3 relevant skills if possible, and format as a bullet list.
Format the output as plain text with bullet points, e.g., '• Python\n• JavaScript\n• SQL'.
""",
                "certifications": f"""
You are a resume writing assistant. The user has provided the following certifications: '{user_input}'.
Based on this, generate a professional certifications section for a resume. Include the certification name, issuing organization, and year (e.g., 2023). If details are missing, make reasonable assumptions.
Format the output as plain text, e.g., 'Certified Python Developer, XYZ Institute, 2023'.
""",
                "languages": f"""
You are a resume writing assistant. The user has provided the following languages: '{user_input}'.
Based on this, generate a professional languages section for a resume. Include proficiency levels (e.g., Fluent, Intermediate) and format as a list.
Format the output as plain text, e.g., 'English (Fluent), Spanish (Intermediate)'.
""",
                "hobbies": f"""
You are a resume writing assistant. The user has provided the following hobbies: '{user_input}'.
Based on this, generate a professional hobbies section for a resume. Expand with 1-2 related hobbies if possible, and format as a list.
Format the output as plain text with bullet points, e.g., '• Reading\n• Hiking'.
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
                content = res.choices[0].message.content.strip()
                if isinstance(content, list):
                    content = '\n'.join(content).strip()
                return content
            except Exception as e:
                logger.error(f"Error generating {section_name}: {str(e)}")
                return user_input

        if summary.strip():
            summary = generate_section_content("summary", summary)
        else:
            prompt = f"""
You are a resume writing assistant. Based on the following:
Education: {education}
Experience: {experience}
Skills: {skills}
Write a 2-3 line professional summary for a resume.
"""
            try:
                from openai import OpenAI
                client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
                res = client.chat.completions.create(
                    model="gpt-3.5-turbo",
                    messages=[{"role": "user", "content": prompt}]
                )
                summary = res.choices[0].message.content.strip()
                if isinstance(summary, list):
                    summary = '\n'.join(summary).strip()
            except Exception as e:
                logger.error(f"Error generating summary: {str(e)}")
                summary = "Unable to generate summary. Please check if the API key is set."

        education = generate_section_content("education", education)
        experience = generate_section_content("experience", experience)
        skills = generate_section_content("skills", skills)
        certifications = generate_section_content("certifications", certifications)
        languages = generate_section_content("languages", languages)
        hobbies = generate_section_content("hobbies", hobbies)

        def section_html(title, content):
            if not content.strip():
                return ""
            html_content = content.strip().replace("\n", "<br>")
            return f"""
            <div class='section' style='margin-bottom:1.2rem;'>
              <h3 style='font-size:0.95rem; line-height:1.3; color:#222; margin-bottom:4px; border-bottom:1px solid #ccc;'>{title}</h3>
              <div>{html_content}</div>
            </div>
            """

        top = f"""
        <div style='text-align:center; margin-bottom: 1.2rem;'>
          <div style='font-size:1.3rem; font-weight:bold; color:#1D75E5;'>{name}</div>
          <div style='font-size:0.9rem; color:#333;'>{email} | {phone} | {location}</div>
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
        return jsonify({'success': False, 'error': str(e)})

@app.route('/analyze-jd', methods=['POST'])
def analyze_jd():
    try:
        data = request.get_json()
        jd_text = data.get('job_description', '')
        if not jd_text:
            return jsonify({'error': 'No job description provided'}), 400
        result = analyze_job_description(jd_text)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': f'Failed to analyze job description: {str(e)}'}), 500

@app.route('/convert-format', methods=['POST'])
def convert_format():
    file = request.files.get('file')
    target_format = request.form.get('target_format')

    if not file or not target_format:
        logger.error("Missing file or target format in request")
        return jsonify({'error': 'Missing file or target format'}), 400

    filename = secure_filename(file.filename)
    ext = os.path.splitext(filename)[1].lower()
    allowed_extensions = {'.pdf', '.docx'}
    if ext not in allowed_extensions:
        logger.error(f"Unsupported file format: {ext}")
        return jsonify({'error': f'Unsupported file format: {ext}. Please upload a PDF or DOCX file.'}), 400

    upload_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    output_path = os.path.join(app.config['UPLOAD_FOLDER'], f"converted_{uuid.uuid4()}.{target_format}")
    html_temp_path = os.path.join(app.config['UPLOAD_FOLDER'], f"temp_{uuid.uuid4()}.html")

    try:
        file.save(upload_path)
        logger.debug(f"Saved uploaded file to {upload_path}")

        if not os.path.exists(upload_path):
            logger.error(f"File not found after saving: {upload_path}")
            return jsonify({'error': 'File could not be saved properly'}), 500

        os.chmod(upload_path, 0o644)
        file_size = os.path.getsize(upload_path) / 1024
        if file_size == 0:
            logger.error(f"Uploaded file is empty: {upload_path}")
            return jsonify({'error': 'Uploaded file is empty'}), 400

        logger.debug(f"File size: {file_size:.2f} KB")

        if ext == '.pdf':
            try:
                with open(upload_path, 'rb') as f:
                    pdf_reader = PyPDF2.PdfReader(f)
                    if pdf_reader.is_encrypted:
                        logger.error("Uploaded PDF is encrypted")
                        return jsonify({'error': 'PDF is encrypted. Please upload an unencrypted PDF.'}), 400
            except Exception as e:
                logger.error(f"Invalid or corrupted PDF file: {str(e)}")
                return jsonify({'error': 'Invalid or corrupted PDF file. Please upload a valid PDF.'}), 400

        if ext == '.docx':
            try:
                doc = Document(upload_path)
            except Exception as e:
                logger.error(f"Invalid or corrupted DOCX file: {str(e)}")
                return jsonify({'error': 'Invalid or corrupted DOCX file. Please upload a valid DOCX.'}), 400

        if target_format == 'text':
            text = ""
            if ext == '.pdf':
                try:
                    doc = fitz.open(upload_path)
                    if doc.page_count == 0:
                        logger.error("PDF has no pages")
                        return jsonify({'error': 'PDF file has no pages to extract text from'}), 400
                    text = "\n".join([page.get_text() for page in doc])
                    doc.close()
                    logger.info(f"Extracted text with PyMuPDF: {len(text)} characters")
                except Exception as e:
                    logger.warning(f"PyMuPDF failed to extract text: {str(e)}")

            elif ext == '.docx':
                try:
                    doc = Document(upload_path)
                    text = "\n".join([para.text for para in doc.paragraphs])
                    logger.info(f"Extracted text from DOCX: {len(text)} characters")
                except Exception as e:
                    logger.error(f"Error extracting text from DOCX: {str(e)}")
                    return jsonify({'error': f'Text extraction from DOCX failed: {str(e)}'}), 500

            if not text.strip():
                logger.warning("No text extracted from file after all attempts")
                return jsonify({'error': 'No text could be extracted from the file. It might be empty, contain only images, or be unreadable.'}), 400

            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(text)
            
            return send_file(
                output_path,
                as_attachment=True,
                download_name="extracted-text.txt",
                mimetype="text/plain"
            )

        elif ext == '.docx' and target_format == 'pdf':
            try:
                doc = Document(upload_path)
                paragraphs = [para.text for para in doc.paragraphs if para.text.strip()]
                if not paragraphs:
                    logger.error("DOCX file is empty or has no readable content")
                    return jsonify({'error': 'DOCX file is empty or has no readable content'}), 400

                html_content = ''.join([f"<p>{para}</p>" for para in paragraphs])
                
                with open(html_temp_path, 'w', encoding='utf-8') as f:
                    f.write(html_content)
                
                pdfkit.from_file(html_temp_path, output_path)
                
                return send_file(
                    output_path,
                    as_attachment=True,
                    download_name="converted.pdf",
                    mimetype="application/pdf"
                )
            except Exception as e:
                logger.error(f"DOCX to PDF conversion failed: {str(e)}")
                return jsonify({'error': f'DOCX to PDF conversion failed: {str(e)}'}), 500

        elif ext == '.pdf' and target_format == 'docx':
            text = ""
            try:
                doc = fitz.open(upload_path)
                if doc.page_count == 0:
                    logger.error("PDF has no pages")
                    return jsonify({'error': 'PDF file has no pages to convert'}), 400
                text = "\n".join([page.get_text() for page in doc])
                doc.close()
                logger.info(f"Extracted text with PyMuPDF for DOCX conversion: {len(text)} characters")
            except Exception as e:
                logger.warning(f"PyMuPDF (fitz) failed to extract text for DOCX conversion: {str(e)}")

            if not text.strip():
                logger.warning("No text extracted from PDF for DOCX conversion after all attempts")
                return jsonify({'error': 'No text could be extracted from the PDF for conversion. It might contain only images or be unreadable.'}), 400

            try:
                word_doc = Document()
                word_doc.add_paragraph(text)
                word_doc.save(output_path)
                logger.info("Successfully converted PDF to DOCX")
                return send_file(
                    output_path,
                    as_attachment=True,
                    download_name="converted.docx",
                    mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                )
            except Exception as e:
                logger.error(f"Failed to create DOCX file: {str(e)}")
                return jsonify({'error': f'Failed to create DOCX file: {str(e)}'}), 500

        else:
            logger.error("Invalid conversion request")
            return jsonify({'error': 'Invalid conversion request. Only PDF to DOCX, DOCX to PDF, or text extraction are supported.'}), 400

    except Exception as e:
        logger.error(f"Error in /convert-format: {str(e)}")
        return jsonify({'error': f'Failed to process file: {str(e)}'}), 500
    finally:
        cleanup_file(upload_path)
        cleanup_file(output_path)
        cleanup_file(html_temp_path)

@app.route('/fix-formatting', methods=['POST'])
def fix_formatting():
    file = request.files.get('file') or request.files.get('resume')
    if not file:
        return jsonify({'error': 'No file uploaded'}), 400

    filename = secure_filename(file.filename)
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    try:
        file.save(filepath)
        result = fix_resume_formatting(filepath)
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error in /fix-formatting: {str(e)}")
        return jsonify({'error': 'Failed to process resume formatting'}), 500
    finally:
        cleanup_file(filepath)

@app.route("/generate-resume-summary", methods=["POST"])
def generate_resume_summary_api():
    data = request.get_json()
    name = data.get("name", "")
    role = data.get("role", "")
    experience = data.get("experience", "")
    skills = data.get("skills", "")

    if not name or not role or not experience or not skills:
        return jsonify({"error": "Missing required fields"}), 400

    summary = generate_resume_summary(name, role, experience, skills)
    return jsonify({"summary": summary})

@app.route('/send-feedback', methods=['POST'])
def send_feedback():
    try:
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        from email.mime.base import MIMEBase
        from email import encoders
        import smtplib

        data = request.form
        name = data.get('name', 'Unknown')
        email = data.get('email', '')
        message = data.get('message', '')
        file = request.files.get('screenshot')

        sender_email = "help@resumefixerpro.com"
        receiver_email = "help@resumefixerpro.com"
        smtp_server = "smtp.hostinger.com"
        smtp_port = 465
        smtp_password = os.environ.get("SMTP_PASSWORD")

        msg = MIMEMultipart()
        msg['From'] = sender_email
        msg['To'] = receiver_email
        msg['Subject'] = "New Feedback Submission from ResumeFixerPro"

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

            part = MIMEBase('application', 'octet-stream')
            with open(filepath, 'rb') as attachment:
                part.set_payload(attachment.read())
            encoders.encode_base64(part)
            part.add_header('Content-Disposition', f'attachment; filename= {filename}')
            msg.attach(part)

        server = smtplib.SMTP_SSL(smtp_server, smtp_port)
        server.login(sender_email, smtp_password)
        server.send_message(msg)
        server.quit()

        if file:
            os.remove(filepath)

        return jsonify({"success": True, "message": "Feedback sent successfully."})
    except Exception as e:
        logger.error(f"Error sending feedback: {str(e)}")
        return jsonify({"success": False, "error": str(e)})

@app.route('/ask-ai', methods=['POST'])
def ask_ai():
    try:
        from openai import OpenAI
        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

        data = request.get_json()
        question = data.get("question", "")
        if not question.strip():
            return jsonify({"answer": "ÃƒÂ¢Ã¯Â¿Â½Ã…â€™ Please enter a question first."})

        system_prompt = {
            "role": "system",
            "content": (
                "You are ResumeBot, the official AI assistant of ResumeFixerPro.com.\n\n"
                "You help users improve resumes, get AI suggestions, download resume templates, generate cover letters, "
                "and check ATS (Applicant Tracking System) compatibility ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬ï¿½ all for free.\n\n"
                "Website Overview:\n"
                "- Website: https://resumefixerpro.com\n"
                "- Owner: Imran Ali (YouTuber & Developer from India)\n"
                "- Global Delivery: Hosted worldwide using Cloudflare CDN for fast, global access\n"
                "- Privacy: ResumeFixerPro respects user privacy. No signup required. No resumes are stored.\n"
                "- Cost: 100% Free to use. No hidden charges. No login required.\n\n"
                "Key Features of ResumeFixerPro:\n"
                "1. AI Resume Fixer Tool ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬â€œ Upload your resume and get instant improvement suggestions with AI fixes.\n"
                "2. Resume Score Checker ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬â€œ See how strong your resume is (0 to 100).\n"
                "3. ATS Compatibility Checker ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬â€œ Check if your resume is ATS-friendly.\n"
                "4. Cover Letter Generator ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬â€œ Instantly generate a job-specific cover letter.\n"
                "5. Resume Template Builder ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬â€œ Choose from 5 student-friendly templates, edit live, and download as PDF/DOCX.\n"
                "6. AI Resume Generator ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬â€œ Fill out a simple form and get a full professional resume in seconds.\n\n"
                "Guidelines:\n"
                "- Always give short, helpful, and positive replies.\n"
                "- If someone asks about the site, privacy, location, features, or Imran Ali, give accurate info.\n"
                "- If asked something unrelated, politely redirect to resume or career help.\n"
                "- Avoid saying 'I don't know.' You are trained to assist users with anything related to ResumeFixerPro.\n\n"
                "Example answers:\n"
                "- 'ResumeFixerPro is a free AI tool created by Imran Ali. No signup needed, and we never store your data.'\n"
                "- 'This site supports global users via Cloudflare, so you can access it from anywhere quickly.'\n"
                "- 'Yes! We have resume templates, ATS checkers, and even instant resume scores.'"
            )
        }

        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                system_prompt,
                {"role": "user", "content": question}
            ]
        )

        answer = response.choices[0].message.content.strip()
        return jsonify({"answer": f"ÃƒÂ°Ã…Â¸Ã‚Â¤Ã¢â‚¬â€œ ResumeBot:\n{answer}"})

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"answer": "ÃƒÂ¢Ã…Â¡Ã‚ ÃƒÂ¯Ã‚Â¸Ã¯Â¿Â½ AI error: " + str(e)})

@app.route('/send-message', methods=['POST'])
def send_message():
    try:
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart
        import smtplib

        data = request.get_json()
        name = data.get('name', 'Unknown')
        email = data.get('email', '')
        message = data.get('message', '')

        sender_email = "help@resumefixerpro.com"
        receiver_email = "help@resumefixerpro.com"
        smtp_server = "smtp.hostinger.com"
        smtp_port = 465
        smtp_password = os.environ.get("SMTP_PASSWORD")

        msg = MIMEMultipart()
        msg['From'] = sender_email
        msg['To'] = receiver_email
        msg['Subject'] = "ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã‚Â¬ New Contact Message from ResumeFixerPro"

        body = f"""
New message from Contact Us page:

Name: {name}
Email: {email}

Message:
{message}
        """.strip()

        msg.attach(MIMEText(body, 'plain'))

        server = smtplib.SMTP_SSL(smtp_server, smtp_port)
        server.login(sender_email, smtp_password)
        server.send_message(msg)
        server.quit()

        return jsonify({"success": True, "message": "Message sent successfully!"})
    except Exception as e:
        logger.error(f"Error in /send-message: {str(e)}")
        return jsonify({"success": False, "error": str(e)})

@app.route('/extract-sections', methods=['POST'])
def extract_sections():
    data = request.get_json()
    text = data.get("text", "")
    if not text.strip():
        return jsonify({"error": "No resume text provided"}), 400

    try:
        sections = extract_resume_sections(text)
        return jsonify(sections)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    logger.info(f"Starting Flask app on port {port}")
    app.run(host="0.0.0.0", port=port)

logger.info("Flask app initialization complete.")
