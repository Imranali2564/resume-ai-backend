import logging
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from werkzeug.utils import secure_filename
import os
import uuid
import json
import re
from resume_ai_analyzer import generate_resume_summary
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
        extract_resume_sections,
        extract_keywords_from_jd,
        extract_text_from_resume,
        compare_resume_with_keywords,
        analyze_job_description,
        fix_resume_formatting,
        generate_section_content
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
        logger.error(f"Error cleaning up file: {filepath}, {str(e)}")

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({"status": "healthy", "message": "App is running successfully"}), 200

@app.route('/upload', methods=['POST'])
def upload_resume():
    file = request.files.get('file')
    atsfix = request.form.get('atsfix') == 'true'
    if not file or file.filename == '':
        return jsonify({'error': 'No file uploaded'}), 400
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(file.filename))
    try:
        file.save(filepath)
        result = analyze_resume_with_openai(filepath, atsfix=atsfix)
        if "error" in result:
            logger.warning(f"Failed to analyze resume: {result['error']}")
            return jsonify({"suggestions": "Unable to generate suggestions. Please check if the API key is set."})
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error in /upload: {str(e)}")
        return jsonify({"suggestions": "Unable to generate suggestions. Please check if the API key is set."})
    finally:
        cleanup_file(filepath)

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

@app.route('/check-ats', methods=['POST'])
def check_ats():
    if 'file' not in request.files:
        logger.error("No file part in the request")
        return jsonify({'error': 'No file part in the request'}), 400

    file = request.files['file']
    if not file or file.filename == '':
        logger.error("No file selected for upload")
        return jsonify({'error': 'No file selected for upload'}), 400

    ext = os.path.splitext(file.filename)[1].lower()
    allowed_extensions = {'.pdf', '.docx'}
    if ext not in allowed_extensions:
        logger.error(f"Unsupported file format: {ext}")
        return jsonify({'error': f'Unsupported file format: {ext}. Please upload a PDF or DOCX file.'}), 400

    filename = secure_filename(file.filename)
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    try:
        file.save(filepath)
        file_size = os.path.getsize(filepath) / 1024
        logger.debug(f"File saved: {filepath}, Size: {file_size:.2f} KB, Extension: {ext}")

        ats_result = check_ats_compatibility(filepath)
        if not ats_result or "Failed to analyze" in ats_result:
            logger.warning("ATS check returned empty or failed result")
            return jsonify({'ats_report': "Unable to perform ATS check. Please check if the API key is set."})
        logger.info("ATS check completed successfully")
        return jsonify({'ats_report': ats_result})
    except Exception as e:
        logger.error(f"Error in /check-ats: {str(e)}")
        return jsonify({'ats_report': "Unable to perform ATS check. Please check if the API key is set."})
    finally:
        cleanup_file(filepath)

@app.route('/parse-resume', methods=['POST'])
def parse_resume():
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

        sections = extract_resume_sections(resume_text)

        # Ensure all section content is a string, not a list
        for key in sections:
            if isinstance(sections[key], list):
                sections[key] = '\n'.join(sections[key]).strip()

        logger.debug(f"Detected sections: {json.dumps(sections, indent=2)}")

        return jsonify({"sections": sections})
    except Exception as e:
        logger.error(f"Error in /parse-resume: {str(e)}")
        return jsonify({'error': f'Failed to parse resume: {str(e)}'}), 500
    finally:
        cleanup_file(filepath)

@app.route("/fix-suggestion", methods=["POST"])
def fix_suggestion():
    try:
        data = request.get_json()
        suggestion = data.get("suggestion")
        full_text = data.get("full_resume_text")

        if not suggestion or not full_text:
            return jsonify({"error": "Missing suggestion or full resume text"}), 400

        result = generate_section_content(suggestion, full_text)

        # Ensure fixedContent is a string, not a list
        if "fixedContent" in result and isinstance(result["fixedContent"], list):
            result["fixedContent"] = '\n'.join(result["fixedContent"]).strip()

        return jsonify(result)

    except Exception as e:
        return jsonify({"error": f"Failed to process suggestion: {str(e)}"}), 500
    
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

@app.route('/final-resume', methods=['POST'])
def final_resume():
    # Get the file and other parameters
    if 'file' not in request.files:
        logger.error("No file part in the request")
        return jsonify({'error': 'No file part in the request'}), 400

    file = request.files['file']
    fixes = json.loads(request.form.get('fixes', '[]'))
    format_type = request.args.get('format', 'docx')
    return_sections = request.args.get('return_sections', 'false') == 'true'

    if not file or file.filename == '':
        logger.error("No file selected for upload")
        return jsonify({'error': 'No file selected for upload'}), 400

    # Validate file extension
    ext = os.path.splitext(file.filename)[1].lower()
    allowed_extensions = {'.pdf', '.docx'}
    if ext not in allowed_extensions:
        logger.error(f"Unsupported file format: {ext}")
        return jsonify({'error': f'Unsupported file format: {ext}. Please upload a PDF or DOCX file.'}), 400

    # Securely construct filepath
    filename = secure_filename(file.filename)
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    output_path = os.path.join(app.config['UPLOAD_FOLDER'], f"fixed_resume_{uuid.uuid4()}.{format_type}")

    try:
        # Ensure the upload directory exists and is writable
        os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
        logger.debug(f"Using filepath: {filepath}, output_path: {output_path}")

        # Save the uploaded file
        try:
            file.save(filepath)
            logger.debug(f"File saved successfully: {filepath}")
        except Exception as e:
            logger.error(f"Failed to save file {filepath}: {str(e)}")
            return jsonify({'error': f'Failed to save uploaded file: {str(e)}'}), 500

        # Verify the file exists and is not empty
        if not os.path.exists(filepath):
            logger.error(f"File not found after saving: {filepath}")
            return jsonify({'error': 'File could not be saved properly'}), 500

        file_size = os.path.getsize(filepath) / 1024  # Size in KB
        if file_size == 0:
            logger.error(f"Uploaded file is empty: {filepath}")
            return jsonify({'error': 'Uploaded file is empty'}), 400
        logger.debug(f"File size: {file_size:.2f} KB")

        # Validate PDF for encryption
        if ext == '.pdf':
            try:
                with open(filepath, 'rb') as f:
                    pdf_reader = PyPDF2.PdfReader(f)
                    if pdf_reader.is_encrypted:
                        logger.error("Uploaded PDF is encrypted")
                        return jsonify({'error': 'PDF is encrypted. Please upload an unencrypted PDF.'}), 400
            except Exception as e:
                logger.error(f"Invalid or corrupted PDF file: {str(e)}")
                return jsonify({'error': 'Invalid or corrupted PDF file. Please upload a valid PDF.'}), 400

        # Validate DOCX for corruption
        if ext == '.docx':
            try:
                doc = Document(filepath)
            except Exception as e:
                logger.error(f"Invalid or corrupted DOCX file: {str(e)}")
                return jsonify({'error': 'Invalid or corrupted DOCX file. Please upload a valid DOCX.'}), 400

        # Extract text from the file
        try:
            if ext == ".pdf":
                resume_text = extract_text_from_pdf(filepath)
                if not resume_text.strip():
                    logger.warning("No text extracted from PDF. It might be image-based.")
                    return jsonify({'error': 'No extractable text found in PDF. It might be image-based.'}), 400
            elif ext == ".docx":
                resume_text = extract_text_from_docx(filepath)
            else:
                return jsonify({'error': 'Unsupported file format'}), 400
        except Exception as e:
            logger.error(f"Error extracting text from file {filepath}: {str(e)}")
            return jsonify({'error': f'Failed to extract text from file: {str(e)}'}), 500

        if not resume_text:
            logger.error(f"No text extracted from file: {filepath}")
            return jsonify({'error': 'No extractable text found in resume'}), 400

        # If resume_text is a dict (possible output from extract_text_from_*), convert to string
        if isinstance(resume_text, dict):
            resume_text = resume_text.get("formatted_text") or resume_text.get("text") or json.dumps(resume_text, indent=2)
            logger.debug(f"Converted resume_text from dict to string: {resume_text[:100]}...")

        if not resume_text.strip():
            logger.error(f"Extracted text is empty after conversion: {filepath}")
            return jsonify({'error': 'No extractable text found in resume after conversion'}), 400

        # Extract sections from the resume text
        try:
            original_sections = extract_resume_sections(resume_text)
            logger.debug(f"Original sections: {json.dumps(original_sections, indent=2)}")
        except Exception as e:
            logger.error(f"Error extracting sections from resume text: {str(e)}")
            return jsonify({'error': f'Failed to extract resume sections: {str(e)}'}), 500

        # Apply fixes to sections
        fixed_sections = {}
        for fix in fixes:
            if "section" in fix:
                section = fix.get('section')
                fixed_text = fix.get('fixedText') or fix.get('fixedContent')
                if section and fixed_text:
                    # Ensure fixed_text is a string
                    if isinstance(fixed_text, list):
                        fixed_text = '\n'.join(fixed_text).strip()
                    fixed_sections[section] = fixed_text
            elif "sections" in fix:
                for section_fix in fix.get('sections', []):
                    section = section_fix.get('section')
                    fixed_text = section_fix.get('fixedContent')
                    if section and fixed_text:
                        # Ensure fixed_text is a string
                        if isinstance(fixed_text, list):
                            fixed_text = '\n'.join(fixed_text).strip()
                        fixed_sections[section] = fixed_text

        logger.debug(f"Fixed sections: {json.dumps(fixed_sections, indent=2)}")

        # Merge original and fixed sections
        final_sections = original_sections.copy()
        for section, content in fixed_sections.items():
            final_sections[section] = content

        # Add a default "Languages" section if missing
        if not final_sections.get("languages"):
            final_sections["languages"] = "English (Fluent)\nHindi (Native)"

        # Extract contact details from "miscellaneous" or directly from resume_text
        email = phone = location = ""
        if final_sections.get("miscellaneous"):
            for line in final_sections["miscellaneous"].splitlines():
                line = line.strip()
                if not email and re.search(r'[\w\.-]+@[\w\.-]+', line):
                    email = re.search(r'[\w\.-]+@[\w\.-]+', line).group()
                if not phone and re.search(r'\+?\d[\d\s\-]{8,}', line):
                    phone = re.search(r'\+?\d[\d\s\-]{8,}', line).group()
                if not location and re.search(r'\b(?:[A-Z][a-z]+(?:,\s*)?)+\b', line) and "India" in line:
                    location = re.search(r'\b(?:[A-Z][a-z]+(?:,\s*)?)+\b', line).group()
        else:
            for line in resume_text.splitlines():
                line = line.strip()
                if not email and re.search(r'[\w\.-]+@[\w\.-]+', line):
                    email = re.search(r'[\w\.-]+@[\w\.-]+', line).group()
                if not phone and re.search(r'\+?\d[\d\s\-]{8,}', line):
                    phone = re.search(r'\+?\d[\d\s\-]{8,}', line).group()
                if not location and re.search(r'\b(?:[A-Z][a-z]+(?:,\s*)?)+\b', line) and "India" in line:
                    location = re.search(r'\b(?:[A-Z][a-z]+(?:,\s*)?)+\b', line).group()

        # Remove "miscellaneous" section
        if "miscellaneous" in final_sections:
            del final_sections["miscellaneous"]

        # Create "personal_details" section with contact details
        contact_details = []
        if email:
            contact_details.append(f"Email: {email}")
        if phone:
            contact_details.append(f"Phone: {phone}")
        if location:
            contact_details.append(f"Location: {location}")
        final_sections["personal_details"] = "\n".join(contact_details)

        # Deduplicate sections by re-running extract_resume_sections on the merged content
        merged_text = ""
        for section, content in final_sections.items():
            if content:
                display_name = {
                    "personal_details": "Personal Details",
                    "summary": "Summary",
                    "skills": "Skills",
                    "experience": "Experience",
                    "education": "Education",
                    "certifications": "Certifications",
                    "languages": "Languages",
                    "hobbies": "Hobbies",
                    "additional_courses": "Additional Courses",
                    "projects": "Projects",
                    "volunteer_experience": "Volunteer Experience",
                    "achievements": "Achievements",
                    "publications": "Publications",
                    "references": "References",
                    "miscellaneous": "Miscellaneous"
                }.get(section, section.replace("_", " ").title())
                merged_text += f"{display_name}\n{content}\n\n"

        try:
            final_sections = extract_resume_sections(merged_text)
            # Ensure all section content is a string
            for key in final_sections:
                if isinstance(final_sections[key], list):
                    final_sections[key] = '\n'.join(final_sections[key]).strip()
            logger.debug(f"Final deduplicated sections: {json.dumps(final_sections, indent=2)}")
        except Exception as e:
            logger.error(f"Error deduplicating sections: {str(e)}")
            return jsonify({'error': f'Failed to deduplicate resume sections: {str(e)}'}), 500

        # Extract name from the first line of resume_text or personal_details
        name = ""
        for line in resume_text.splitlines():
            line = line.strip()
            if re.search(r'^[A-Z][a-z]+\s[A-Z][a-z]+', line):
                name = line
                break
        if not name and final_sections.get("personal_details"):
            for line in final_sections["personal_details"].splitlines():
                if re.search(r'^[A-Z][a-z]+\s[A-Z][a-z]+', line):
                    name = line
                    break
        if not name:
            name = "Riya Sharma"  # Fallback to default name from original resume

        # If return_sections is true, return the sections as JSON
        if return_sections:
            return jsonify({"sections": final_sections})

        # Generate DOCX format
        if format_type == 'docx':
            try:
                doc = Document()
                for s in doc.sections:
                    s.top_margin = s.bottom_margin = Inches(0.5)
                    s.left_margin = s.right_margin = Inches(0.75)

                # Add only the name at the top
                heading = doc.add_heading(name, level=1)
                heading.alignment = WD_ALIGN_PARAGRAPH.CENTER
                run = heading.runs[0]
                run.font.size = Pt(16)
                run.bold = True
                run.font.color.rgb = RGBColor(0, 113, 188)

                doc.add_paragraph()  # Add spacing after the name

                section_display_names = {
                    "personal_details": "Personal Details",
                    "summary": "Professional Summary",
                    "skills": "Skills",
                    "experience": "Experience",
                    "education": "Education",
                    "certifications": "Certifications",
                    "languages": "Languages",
                    "hobbies": "Hobbies",
                    "additional_courses": "Additional Courses",
                    "projects": "Projects",
                    "volunteer_experience": "Volunteer Experience",
                    "achievements": "Achievements",
                    "publications": "Publications",
                    "references": "References",
                    "miscellaneous": "Miscellaneous"
                }

                # Ensure "personal_details" is the first section
                ordered_sections = [("personal_details", final_sections.get("personal_details", ""))]
                for section_key, content in final_sections.items():
                    if section_key != "personal_details" and content:
                        ordered_sections.append((section_key, content))

                for section_key, content in ordered_sections:
                    if content:
                        display_name = section_display_names.get(section_key, ' '.join(word.capitalize() for word in section_key.split('_')))
                        p = doc.add_paragraph()
                        run = p.add_run(display_name.upper())
                        run.bold = True
                        run.font.color.rgb = RGBColor(255, 255, 255)
                        shading = parse_xml(r'<w:shd {} w:fill="0071BC"/>'.format(nsdecls('w')))
                        p._element.get_or_add_pPr().append(shading)

                        for line in content.splitlines():
                            if line:
                                bullet_sections = ["skills", "experience", "hobbies", "additional_courses", "projects", "volunteer_experience", "achievements"]
                                para = doc.add_paragraph(style='List Bullet' if section_key in bullet_sections else None)
                                para.add_run(line)

                # Ensure the output directory is writable
                os.makedirs(os.path.dirname(output_path), exist_ok=True)
                doc.save(output_path)
                if not os.path.exists(output_path):
                    logger.error(f"DOCX file not created at {output_path}")
                    return jsonify({'error': 'Failed to create DOCX file on the server'}), 500
                logger.info(f"DOCX file created successfully at {output_path}")
                return send_file(output_path, as_attachment=True, download_name="Fixed_Resume.docx",
                                mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
            except Exception as e:
                logger.error(f"Error generating DOCX file: {str(e)}")
                return jsonify({'error': f'Failed to generate DOCX file: {str(e)}'}), 500

        # Generate PDF format
        elif format_type == 'pdf':
            try:
                # Ensure the output directory is writable
                os.makedirs(os.path.dirname(output_path), exist_ok=True)

                doc = SimpleDocTemplate(
                    output_path,
                    pagesize=letter,
                    leftMargin=0.75 * inch,
                    rightMargin=0.75 * inch,
                    topMargin=0.5 * inch,
                    bottomMargin=0.5 * inch
                )
                styles = getSampleStyleSheet()

                # Define styles with error handling
                styles.add(ParagraphStyle(name='Name', fontSize=16, alignment=1, spaceAfter=6, textColor=HexColor('#0071BC')))
                styles.add(ParagraphStyle(name='SectionHeading', fontSize=12, spaceBefore=10, spaceAfter=5, textColor=HexColor('#FFFFFF')))
                styles.add(ParagraphStyle(name='Body', fontSize=11, spaceAfter=6, textColor=HexColor('#323232')))
                if 'Bullet' not in styles.byName:
                    styles.add(ParagraphStyle(
                        name='Bullet',
                        fontSize=11,
                        spaceAfter=6,
                        leftIndent=0.5 * inch,
                        firstLineIndent=-0.25 * inch,
                        bulletFontName='Times-Roman',
                        bulletFontSize=11,
                        bulletIndent=0.25 * inch,
                        textColor=HexColor('#323232')
                    ))

                # Build the PDF content
                story = [
                    Paragraph(f"<b>{name}</b>", styles['Name']),
                    Spacer(1, 12)
                ]

                section_display_names = {
                    "personal_details": "Personal Details",
                    "summary": "Professional Summary",
                    "skills": "Skills",
                    "experience": "Experience",
                    "education": "Education",
                    "certifications": "Certifications",
                    "languages": "Languages",
                    "hobbies": "Hobbies",
                    "additional_courses": "Additional Courses",
                    "projects": "Projects",
                    "volunteer_experience": "Volunteer Experience",
                    "achievements": "Achievements",
                    "publications": "Publications",
                    "references": "References",
                    "miscellaneous": "Miscellaneous"
                }

                # Ensure "personal_details" is the first section
                ordered_sections = [("personal_details", final_sections.get("personal_details", ""))]
                for section_key, content in final_sections.items():
                    if section_key != "personal_details" and content:
                        ordered_sections.append((section_key, content))

                for section_key, content in ordered_sections:
                    if content:
                        display_name = section_display_names.get(section_key, ' '.join(word.capitalize() for word in section_key.split('_')))
                        heading = Table([[Paragraph(f"<b>{display_name.upper()}</b>", styles['SectionHeading'])]], colWidths=[6.5 * inch])
                        heading.setStyle(TableStyle([
                            ('BACKGROUND', (0, 0), (-1, -1), HexColor('#0071BC')),
                            ('TEXTCOLOR', (0, 0), (-1, -1), HexColor('#FFFFFF')),
                            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                            ('FONTNAME', (0, 0), (-1, -1), 'Times-Roman'),
                            ('FONTSIZE', (0, 0), (-1, -1), 12),
                            ('LEFTPADDING', (0, 0), (-1, -1), 10),
                            ('RIGHTPADDING', (0, 0), (-1, -1), 10),
                            ('TOPPADDING', (0, 0), (-1, -1), 5),
                            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
                        ]))
                        story.append(heading)

                        for line in content.splitlines():
                            if line:
                                bullet_sections = ["skills", "experience", "hobbies", "additional_courses", "projects", "volunteer_experience", "achievements"]
                                if section_key in bullet_sections:
                                    story.append(Paragraph(f"â€¢ {line}", styles['Bullet']))
                                else:
                                    story.append(Paragraph(line, styles['Body']))

                # Generate the PDF
                doc.build(story)
                if not os.path.exists(output_path):
                    logger.error(f"PDF file not created at {output_path}")
                    return jsonify({'error': 'Failed to create PDF file on the server'}), 500
                logger.info(f"PDF file created successfully at {output_path}")
                return send_file(output_path, as_attachment=True, download_name="Fixed_Resume.pdf", mimetype="application/pdf")
            except Exception as e:
                logger.error(f"Error generating PDF file: {str(e)}")
                return jsonify({'error': f'Failed to generate PDF file: {str(e)}'}), 500

        else:
            return jsonify({'error': 'Invalid format specified'}), 400

    except Exception as e:
        logger.error(f"Error in /final-resume: {str(e)}")
        return jsonify({'error': f'Failed to generate resume: {str(e)}'}), 500
    finally:
        cleanup_file(filepath)
        cleanup_file(output_path)

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
Format the output as plain text with bullet points, e.g., 'â€¢ Python\nâ€¢ JavaScript\nâ€¢ SQL'.
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
Format the output as plain text with bullet points, e.g., 'â€¢ Reading\nâ€¢ Hiking'.
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
                    messages=[
                        {"role": "user", "content": prompt}
                    ]
                )
                content = res.choices[0].message.content.strip()
                # Ensure the content is a string, not a list
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
                    messages=[
                        {"role": "user", "content": prompt}
                    ]
                )
                summary = res.choices[0].message.content.strip()
                # Ensure summary is a string
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
        html += section_html("Summary", summary)
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

@app.route('/generate-cover-letter', methods=['POST'])
def generate_cover_letter():
    file = request.files.get('file')
    job_title = request.form.get('job_title')
    company_name = request.form.get('company_name')

    if not file or not job_title or not company_name:
        return jsonify({'error': 'File, job title, and company name are required'}), 400

    filename = secure_filename(file.filename)
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    try:
        file.save(filepath)
        logger.debug(f"Saved file to {filepath}")
        ext = os.path.splitext(filename)[1].lower()
        if ext == '.pdf':
            resume_text = extract_text_from_pdf(filepath)
        elif ext == '.docx':
            resume_text = extract_text_from_docx(filepath)
        else:
            return jsonify({'error': 'Unsupported file format'}), 400

        if not resume_text.strip():
            return jsonify({'error': 'Could not extract text from resume'}), 400

        name = ""
        email = ""
        phone = ""
        location = ""

        for line in resume_text.splitlines():
            line = line.strip()
            if not name and re.search(r'^[A-Z][a-z]+\s[A-Z][a-z]+', line):
                name = line
            if not email and re.search(r'[\w\.-]+@[\w\.-]+', line):
                email = re.search(r'[\w\.-]+@[\w\.-]+', line).group()
            if not phone and re.search(r'\+?\d[\d\s\-]{8,}', line):
                phone = re.search(r'\+?\d[\d\s\-]{8,}', line).group()
            if not location and re.search(r'\b(?:[A-Z][a-z]+(?:,\s*)?)+\b', line):
                location = re.search(r'\b(?:[A-Z][a-z]+(?:,\s*)?)+\b', line).group()
            if name and email and phone and location:
                break

        prompt = f"""
You are a career coach and expert cover letter writer. Based on the resume content and the job title and company name below, write a compelling cover letter.

Candidate Details:
Name: {name}
Email: {email}
Phone: {phone}
Location: {location}

Resume:
{resume_text}

Job Title: {job_title}
Company Name: {company_name}

Cover Letter:
"""

        try:
            from openai import OpenAI
            client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
            response = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": "You are a professional cover letter writing assistant."},
                    {"role": "user", "content": prompt}
                ]
            )
            cover_letter = response.choices[0].message.content.strip()
            return jsonify({"cover_letter": cover_letter})
        except Exception as e:
            logger.error(f"Error in OpenAI API call for /generate-cover-letter: {str(e)}")
            return jsonify({"cover_letter": "Unable to generate cover letter. Please check if the API key is set."})
    except Exception as e:
        logger.error(f"Error generating cover letter: {str(e)}")
        return jsonify({"cover_letter": "Unable to generate cover letter. Please check if the API key is set."})
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
        logger.error(f"Error saving cover letter DOCX file: {str(e)}")
        return jsonify({'error': f'Failed to save cover letter DOCX file: {str(e)}'}), 500
    finally:
        cleanup_file(output_path)
        
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

    # Save the uploaded file temporarily
    upload_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    output_path = os.path.join(app.config['UPLOAD_FOLDER'], f"converted_{uuid.uuid4()}.{target_format}")
    html_temp_path = os.path.join(app.config['UPLOAD_FOLDER'], f"temp_{uuid.uuid4()}.html")

    try:
        # Save the file with proper permissions
        file.save(upload_path)
        logger.debug(f"Saved uploaded file to {upload_path}")

        # Check if file exists and is readable
        if not os.path.exists(upload_path):
            logger.error(f"File not found after saving: {upload_path}")
            return jsonify({'error': 'File could not be saved properly'}), 500

        os.chmod(upload_path, 0o644)  # Ensure file is readable/writable
        file_size = os.path.getsize(upload_path) / 1024
        if file_size == 0:
            logger.error(f"Uploaded file is empty: {upload_path}")
            return jsonify({'error': 'Uploaded file is empty'}), 400

        logger.debug(f"File size: {file_size:.2f} KB")

        # Validate PDF (check for encryption or corruption)
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

        # Validate DOCX (check for corruption)
        if ext == '.docx':
            try:
                doc = Document(upload_path)
            except Exception as e:
                logger.error(f"Invalid or corrupted DOCX file: {str(e)}")
                return jsonify({'error': 'Invalid or corrupted DOCX file. Please upload a valid DOCX.'}), 400

        # Text Extraction (PDF/DOCX to Text)
        if target_format == 'text':
            text = ""
            if ext == '.pdf':
                # Try PyMuPDF (fitz) for text-based PDFs
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

            # Final check for extracted text
            if not text.strip():
                logger.warning("No text extracted from file after all attempts")
                return jsonify({'error': 'No text could be extracted from the file. It might be empty, contain only images, or be unreadable.'}), 400

            # Save the text to a temporary file
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(text)
            
            # Serve the file with proper headers
            return send_file(
                output_path,
                as_attachment=True,
                download_name="extracted-text.txt",
                mimetype="text/plain"
            )

        # DOCX to PDF Conversion
        elif ext == '.docx' and target_format == 'pdf':
            try:
                doc = Document(upload_path)
                paragraphs = [para.text for para in doc.paragraphs if para.text.strip()]
                if not paragraphs:
                    logger.error("DOCX file is empty or has no readable content")
                    return jsonify({'error': 'DOCX file is empty or has no readable content'}), 400

                html_content = ''.join([f"<p>{para}</p>" for para in paragraphs])
                
                # Save HTML content to a temporary file
                with open(html_temp_path, 'w', encoding='utf-8') as f:
                    f.write(html_content)
                
                # Convert HTML to PDF
                pdfkit.from_file(html_temp_path, output_path)
                
                # Serve the PDF file with proper headers
                return send_file(
                    output_path,
                    as_attachment=True,
                    download_name="converted.pdf",
                    mimetype="application/pdf"
                )
            except Exception as e:
                logger.error(f"DOCX to PDF conversion failed: {str(e)}")
                return jsonify({'error': f'DOCX to PDF conversion failed: {str(e)}'}), 500

        # PDF to DOCX Conversion
        elif ext == '.pdf' and target_format == 'docx':
            text = ""
            # Try PyMuPDF (fitz) for text-based PDFs
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

            # Final check for extracted text
            if not text.strip():
                logger.warning("No text extracted from PDF for DOCX conversion after all attempts")
                return jsonify({'error': 'No text could be extracted from the PDF for conversion. It might contain only images or be unreadable.'}), 400

            # Create a new DOCX document
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
        # Clean up all temporary files
        cleanup_file(upload_path)
        cleanup_file(output_path)
        cleanup_file(html_temp_path)
    
@app.route('/fix-formatting', methods=['POST'])
def fix_formatting():
    file = request.files.get('resume')
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

        # Email config
        sender_email = "help@resumefixerpro.com"
        receiver_email = "help@resumefixerpro.com"
        smtp_server = "smtp.hostinger.com"
        smtp_port = 465
        smtp_password = os.environ.get("SMTP_PASSWORD")  # Set this in Render or your .env

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
            return jsonify({"answer": "â�Œ Please enter a question first."})

        system_prompt = {
            "role": "system",
            "content": (
                "You are ResumeBot, the official AI assistant of ResumeFixerPro.com.\n\n"
                "You help users improve resumes, get AI suggestions, download resume templates, generate cover letters, "
                "and check ATS (Applicant Tracking System) compatibility â€” all for free.\n\n"
                "Website Overview:\n"
                "- Website: https://resumefixerpro.com\n"
                "- Owner: Imran Ali (YouTuber & Developer from India)\n"
                "- Global Delivery: Hosted worldwide using Cloudflare CDN for fast, global access\n"
                "- Privacy: ResumeFixerPro respects user privacy. No signup required. No resumes are stored.\n"
                "- Cost: 100% Free to use. No hidden charges. No login required.\n\n"
                "Key Features of ResumeFixerPro:\n"
                "1. AI Resume Fixer Tool â€“ Upload your resume and get instant improvement suggestions with AI fixes.\n"
                "2. Resume Score Checker â€“ See how strong your resume is (0 to 100).\n"
                "3. ATS Compatibility Checker â€“ Check if your resume is ATS-friendly.\n"
                "4. Cover Letter Generator â€“ Instantly generate a job-specific cover letter.\n"
                "5. Resume Template Builder â€“ Choose from 5 student-friendly templates, edit live, and download as PDF/DOCX.\n"
                "6. AI Resume Generator â€“ Fill out a simple form and get a full professional resume in seconds.\n\n"
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
        return jsonify({"answer": f"ðŸ¤– ResumeBot:\n{answer}"})

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"answer": "âš ï¸� AI error: " + str(e)})
    
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
        smtp_password = os.environ.get("SMTP_PASSWORD")  # use Render env var

        msg = MIMEMultipart()
        msg['From'] = sender_email
        msg['To'] = receiver_email
        msg['Subject'] = "ðŸ“¬ New Contact Message from ResumeFixerPro"

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

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    logger.info(f"Starting Flask app on port {port}")
    app.run(host="0.0.0.0", port=port)

logger.info("Flask app initialization complete.")
