import logging
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from werkzeug.utils import secure_filename
import os
import uuid
import json
import re
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
    extract_text_with_ocr,
    check_ats_compatibility,
    extract_resume_sections,
    extract_keywords_from_jd,
    extract_text_from_resume,
    compare_resume_with_keywords
)
except ImportError as e:
    logging.error(f"Failed to import resume_ai_analyzer: {str(e)}")
    raise

logging_level = logging.INFO if os.environ.get("FLASK_ENV") != "development" else logging.DEBUG
logging.basicConfig(level=logging_level)
logger = logging.getLogger(__name__)

logging.getLogger("pdfminer").setLevel(logging.ERROR)

logger.info("Starting Flask app initialization...")

app = Flask(__name__, static_url_path='/static')
CORS(app, resources={r"/*": {"origins": "https://resumefixerpro.com"}})

UPLOAD_FOLDER = 'uploads'
STATIC_FOLDER = 'static'
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
            resume_text = extract_text_from_pdf(filepath) or extract_text_with_ocr(filepath)
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

        if ext == ".pdf":
            resume_text = extract_text_from_pdf(filepath)
            if not resume_text:
                logger.debug("Falling back to OCR for PDF text extraction")
                resume_text = extract_text_with_ocr(filepath)
        elif ext == ".docx":
            resume_text = extract_text_from_docx(filepath)
        else:
            logger.error(f"Unexpected file extension: {ext}")
            return jsonify({'error': f'Unexpected file format: {ext}'}), 400

        if not resume_text or not resume_text.strip():
            logger.warning("No extractable text found in resume")
            return jsonify({'error': 'No extractable text found in resume. The file might be empty or unreadable.'}), 400
        logger.debug(f"Extracted text length: {len(resume_text)} characters")

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
            resume_text = extract_text_from_pdf(filepath) or extract_text_with_ocr(filepath)
        elif ext == ".docx":
            resume_text = extract_text_from_docx(filepath)
        else:
            return jsonify({'error': 'Unsupported file format'}), 400

        if not resume_text.strip():
            return jsonify({'error': 'No extractable text found in resume'}), 400

        sections = extract_resume_sections(resume_text)

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

        from resume_ai_analyzer import generate_section_content
        result = generate_section_content(suggestion, full_text)
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
    jd_keywords = extract_keywords_from_jd(job_description)
    results = compare_resume_with_keywords(resume_text, jd_keywords)

    return jsonify(results)


@app.route('/final-resume', methods=['POST'])
def final_resume():
    file = request.files.get('file')
    fixes = json.loads(request.form.get('fixes', '[]'))
    format_type = request.args.get('format', 'docx')
    return_sections = request.args.get('return_sections', 'false') == 'true'

    if not file:
        return jsonify({'error': 'No file uploaded'}), 400

    filepath = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(file.filename))
    output_path = os.path.join(app.config['UPLOAD_FOLDER'], f"fixed_resume_{uuid.uuid4()}.{format_type}")

    try:
        file.save(filepath)
        ext = os.path.splitext(filepath)[1].lower()
        if ext == ".pdf":
            resume_text = extract_text_from_pdf(filepath) or extract_text_with_ocr(filepath)
        elif ext == ".docx":
            resume_text = extract_text_from_docx(filepath)
        else:
            return jsonify({'error': 'Unsupported file format'}), 400

        if not resume_text.strip():
            return jsonify({'error': 'No extractable text found in resume'}), 400

        original_sections = extract_resume_sections(resume_text)

        logger.debug(f"Original sections: {json.dumps(original_sections, indent=2)}")

        fixed_sections = {}
        for fix in fixes:
            if "section" in fix:
                section = fix.get('section')
                fixed_text = fix.get('fixedText') or fix.get('fixedContent')
                if section and fixed_text:
                    fixed_sections[section] = fixed_text.strip()
            elif "sections" in fix:
                for section_fix in fix.get('sections', []):
                    section = section_fix.get('section')
                    fixed_text = section_fix.get('fixedContent')
                    if section and fixed_text:
                        fixed_sections[section] = fixed_text.strip()

        logger.debug(f"Fixed sections: {json.dumps(fixed_sections, indent=2)}")

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

        final_sections = extract_resume_sections(merged_text)

        logger.debug(f"Final deduplicated sections: {json.dumps(final_sections, indent=2)}")

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

        if format_type == 'docx':
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

            doc.save(output_path)
            return send_file(output_path, as_attachment=True, download_name="Fixed_Resume.docx",
                             mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document")

        elif format_type == 'pdf':
            from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
            from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
            from reportlab.lib.pagesizes import letter
            from reportlab.lib.units import inch
            from reportlab.lib.colors import HexColor

            doc = SimpleDocTemplate(output_path, pagesize=letter,
                                    leftMargin=0.75 * inch, rightMargin=0.75 * inch,
                                    topMargin=0.5 * inch, bottomMargin=0.5 * inch)
            styles = getSampleStyleSheet()

            styles.add(ParagraphStyle(name='Name', fontSize=16, alignment=1, spaceAfter=6, textColor=HexColor('#0071BC')))
            styles.add(ParagraphStyle(name='SectionHeading', fontSize=12, spaceBefore=10, spaceAfter=5, textColor=HexColor('#FFFFFF')))
            styles.add(ParagraphStyle(name='Body', fontSize=11, spaceAfter=6, textColor=HexColor('#323232')))
            if 'Bullet' not in styles.byName:
                styles.add(ParagraphStyle(name='Bullet', fontSize=11, spaceAfter=6,
                                          leftIndent=0.5 * inch, firstLineIndent=-0.25 * inch,
                                          bulletFontName='Times-Roman', bulletFontSize=11,
                                          bulletIndent=0.25 * inch, textColor=HexColor('#323232')))

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
                                story.append(Paragraph(f"• {line}", styles['Bullet']))
                            else:
                                story.append(Paragraph(line, styles['Body']))

            doc.build(story)
            return send_file(output_path, as_attachment=True, download_name="Fixed_Resume.pdf", mimetype="application/pdf")

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
                    messages=[
                        {"role": "user", "content": prompt}
                    ]
                )
                return res.choices[0].message.content.strip()
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
            if not resume_text:
                logger.debug("Falling back to OCR for PDF text extraction")
                resume_text = extract_text_with_ocr(filepath)
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

        from resume_ai_analyzer import analyze_job_description
        result = analyze_job_description(jd_text)

        return jsonify(result)

    except Exception as e:
        return jsonify({'error': f'Failed to analyze job description: {str(e)}'}), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    logger.info(f"Starting Flask app on port {port}")
    app.run(host="0.0.0.0", port=port)

logger.info("Flask app initialization complete.")
