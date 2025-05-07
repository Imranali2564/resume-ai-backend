import logging
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from werkzeug.utils import secure_filename
import os
import uuid
import json
import re
from openai import OpenAI
from docx import Document
from docx.shared import Pt, Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from resume_ai_analyzer import (
    analyze_resume_with_openai,
    extract_text_from_pdf,
    extract_text_from_docx,
    extract_text_with_ocr,
    check_ats_compatibility
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

logger.info("Starting Flask app initialization...")

app = Flask(__name__, static_url_path='/static')
CORS(app, resources={r"/*": {"origins": "https://resumefixerpro.com"}})

UPLOAD_FOLDER = 'uploads'
STATIC_FOLDER = 'static'
try:
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    os.makedirs(STATIC_FOLDER, exist_ok=True)
    logger.info(f"Successfully created directories: {UPLOAD_FOLDER}, {STATIC_FOLDER}")
except Exception as e:
    logger.error(f"Failed to create directories: {str(e)}")
    raise RuntimeError(f"Failed to create directories: {str(e)}")
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Validate OpenAI API key
openai_api_key = os.environ.get("OPENAI_API_KEY")
if not openai_api_key:
    logger.error("OPENAI_API_KEY environment variable is not set")
    raise ValueError("OPENAI_API_KEY environment variable is not set")
try:
    client = OpenAI(api_key=openai_api_key)
    logger.info("Successfully initialized OpenAI client")
except Exception as e:
    logger.error(f"Failed to initialize OpenAI client: {str(e)}")
    raise RuntimeError(f"Failed to initialize OpenAI client: {str(e)}")

@app.route('/upload', methods=['POST'])
def upload_resume():
    file = request.files.get('file')
    atsfix = request.form.get('atsfix') == 'true'
    if not file or file.filename == '':
        return jsonify({'error': 'No file uploaded'}), 400
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(file.filename))
    try:
        file.save(filepath)
    except Exception as e:
        logger.error(f"Error saving file: {str(e)}")
        return jsonify({'error': f'Failed to save file: {str(e)}'}), 500
    try:
        result = analyze_resume_with_openai(filepath, atsfix=atsfix)
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error in /upload: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/resume-score', methods=['POST'])
def resume_score():
    file = request.files.get('file')
    if not file:
        return jsonify({'error': 'No file uploaded'}), 400
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(file.filename))
    try:
        file.save(filepath)
    except Exception as e:
        logger.error(f"Error saving file: {str(e)}")
        return jsonify({'error': f'Failed to save file: {str(e)}'}), 500
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

@app.route('/check-ats', methods=['POST'])
def check_ats():
    file = request.files.get('file')
    if not file:
        return jsonify({'error': 'No file uploaded'}), 400
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(file.filename))
    try:
        file.save(filepath)
    except Exception as e:
        logger.error(f"Error saving file: {str(e)}")
        return jsonify({'error': f'Failed to save file: {str(e)}'}), 500
    try:
        ats_result = check_ats_compatibility(filepath)
        return jsonify({'ats_report': ats_result})
    except Exception as e:
        logger.error(f"Error in /check-ats: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/parse-resume', methods=['POST'])
def parse_resume():
    file = request.files.get('file')
    if not file:
        return jsonify({'error': 'No file uploaded'}), 400
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(file.filename))
    try:
        file.save(filepath)
    except Exception as e:
        logger.error(f"Error saving file: {str(e)}")
        return jsonify({'error': f'Failed to save file: {str(e)}'}), 500

    ext = os.path.splitext(filepath)[1].lower()
    if ext == ".pdf":
        resume_text = extract_text_from_pdf(filepath) or extract_text_with_ocr(filepath)
    elif ext == ".docx":
        resume_text = extract_text_from_docx(filepath)
    else:
        return jsonify({'error': 'Unsupported file format'}), 400

    if not resume_text.strip():
        return jsonify({'error': 'No extractable text found in resume'}), 400

    sections = {
        "skills": "",
        "experience": "",
        "education": "",
        "certifications": "",
        "languages": "",
        "hobbies": ""
    }
    current_section = None
    lines = resume_text.splitlines()

    for line in lines:
        line = line.strip()
        if not line:
            continue
        lower_line = line.lower()
        if "skills" in lower_line:
            current_section = "skills"
        elif "experience" in lower_line:
            current_section = "experience"
        elif "education" in lower_line:
            current_section = "education"
        elif "certifications" in lower_line:
            current_section = "certifications"
        elif "languages" in lower_line:
            current_section = "languages"
        elif "hobbies" in lower_line:
            current_section = "hobbies"
        elif current_section:
            sections[current_section] += line + "\n"

    for key in sections:
        sections[key] = sections[key].strip()

    return jsonify({"sections": sections})

@app.route('/fix-suggestion', methods=['POST'])
def fix_suggestion():
    try:
        data = request.get_json()
        suggestion = data.get('suggestion')
        section = data.get('section')
        section_content = data.get('sectionContent')

        if not suggestion or not section or not section_content:
            return jsonify({'error': 'Missing suggestion, section, or section content'}), 400

        prompt = f"""
You are a resume writing assistant. The user has received the following suggestion for their resume:
Suggestion: {suggestion}

The suggestion applies to the following section of their resume:
Section: {section}
Content: {section_content}

Based on the suggestion, rewrite the content of this section to address the suggestion. Return only the updated content for this section, nothing else.
"""

        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a professional resume writing assistant."},
                {"role": "user", "content": prompt}
            ]
        )
        fixed_content = response.choices[0].message.content.strip()
        return jsonify({"fixedContent": fixed_content})
    except Exception as e:
        logger.error(f"Error in /fix-suggestion: {str(e)}")
        return jsonify({'error': f'Failed to fix suggestion: {str(e)}'}), 500

@app.route('/final-resume', methods=['POST'])
def final_resume():
    file = request.files.get('file')
    fixes = json.loads(request.form.get('fixes', '[]'))
    format_type = request.args.get('format', 'docx')  # Default to docx if format not specified

    if not file:
        return jsonify({'error': 'No file uploaded'}), 400

    filepath = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(file.filename))
    try:
        file.save(filepath)
    except Exception as e:
        logger.error(f"Error saving file: {str(e)}")
        return jsonify({'error': f'Failed to save file: {str(e)}'}), 500

    ext = os.path.splitext(filepath)[1].lower()
    if ext == ".pdf":
        resume_text = extract_text_from_pdf(filepath) or extract_text_with_ocr(filepath)
    elif ext == ".docx":
        resume_text = extract_text_from_docx(filepath)
    else:
        return jsonify({'error': 'Unsupported file format'}), 400

    if not resume_text.strip():
        return jsonify({'error': 'No extractable text found in resume'}), 400

    # Log the fixes for debugging
    logger.info(f"Received fixes: {json.dumps(fixes, indent=2)}")

    # Parse resume into sections
    sections = {
        "skills": "",
        "experience": "",
        "education": "",
        "certifications": "",
        "languages": "",
        "hobbies": ""
    }
    current_section = None
    lines = resume_text.splitlines()

    for line in lines:
        line = line.strip()
        if not line:
            continue
        lower_line = line.lower()
        if "skills" in lower_line:
            current_section = "skills"
        elif "experience" in lower_line:
            current_section = "experience"
        elif "education" in lower_line:
            current_section = "education"
        elif "certifications" in lower_line:
            current_section = "certifications"
        elif "languages" in lower_line:
            current_section = "languages"
        elif "hobbies" in lower_line:
            current_section = "hobbies"
        elif current_section:
            sections[current_section] += line + "\n"

    # Apply fixes to the relevant sections
    for fix in fixes:
        section = fix.get('section')
        fixed_text = fix.get('fixedText')
        if section in sections:
            sections[section] = fixed_text

    # Extract contact information
    name = email = phone = location = ""
    for line in lines:
        line = line.strip()
        if not name and re.search(r'^[A-Z][a-z]+\s[A-Z][a-z]+', line):
            name = line
        if not email and re.search(r'[\w\.-]+@[\w\.-]+', line):
            email = line
        if not phone and re.search(r'\+?\d[\d\s\-]{8,}', line):
            phone = line
        if not location and re.search(r'\b(?:[A-Z][a-z]+(?:,\s*)?)+\b', line):
            location = line

    # Generate DOCX if requested
    if format_type == 'docx':
        doc = Document()
        
        # Set document margins
        sections_doc = doc.sections
        for section in sections_doc:
            section.left_margin = Inches(1)
            section.right_margin = Inches(1)
            section.top_margin = Inches(1)
            section.bottom_margin = Inches(1)

        # Name (Heading)
        name_paragraph = doc.add_heading(name, level=1)
        name_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        name_run = name_paragraph.runs[0]
        name_run.font.name = 'Times New Roman'
        name_run._element.rPr.rFonts.set(qn('w:ascii'), 'Times New Roman')  # Ensure font fallback
        name_run._element.rPr.rFonts.set(qn('w:hAnsi'), 'Times New Roman')
        name_run.font.size = Pt(14)
        name_run.bold = True

        # Contact Info
        contact_info = f"{email} | {phone} | {location}"
        contact_paragraph = doc.add_paragraph(contact_info)
        contact_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        contact_run = contact_paragraph.runs[0]
        contact_run.font.name = 'Times New Roman'
        contact_run._element.rPr.rFonts.set(qn('w:ascii'), 'Times New Roman')
        contact_run._element.rPr.rFonts.set(qn('w:hAnsi'), 'Times New Roman')
        contact_run.font.size = Pt(10)

        doc.add_paragraph()  # Spacer

        # Add sections
        for section_name, content in sections.items():
            if content.strip():
                # Section Heading
                heading = doc.add_heading(section_name.capitalize(), level=2)
                heading_run = heading.runs[0]
                heading_run.font.name = 'Times New Roman'
                heading_run._element.rPr.rFonts.set(qn('w:ascii'), 'Times New Roman')
                heading_run._element.rPr.rFonts.set(qn('w:hAnsi'), 'Times New Roman')
                heading_run.font.size = Pt(12)
                heading_run.bold = True

                # Section Content
                for line in content.splitlines():
                    line = line.strip()
                    if line:
                        if section_name in ["skills", "experience", "hobbies"]:  # Use bullets for these sections
                            p = doc.add_paragraph(style='List Bullet')
                            run = p.add_run(line)
                        else:
                            p = doc.add_paragraph()
                            run = p.add_run(line)
                        run.font.name = 'Times New Roman'
                        run._element.rPr.rFonts.set(qn('w:ascii'), 'Times New Roman')
                        run._element.rPr.rFonts.set(qn('w:hAnsi'), 'Times New Roman')
                        run.font.size = Pt(11)

        output_path = os.path.join(app.config['UPLOAD_FOLDER'], f"fixed_resume_{uuid.uuid4()}.docx")
        try:
            doc.save(output_path)
        except Exception as e:
            logger.error(f"Error saving DOCX file: {str(e)}")
            return jsonify({'error': f'Failed to save DOCX file: {str(e)}'}), 500
        return send_file(output_path, as_attachment=True, download_name="Fixed_Resume.docx")

    # Generate PDF if requested
    elif format_type == 'pdf':
        output_path = os.path.join(app.config['UPLOAD_FOLDER'], f"fixed_resume_{uuid.uuid4()}.pdf")
        doc = SimpleDocTemplate(output_path, pagesize=letter, leftMargin=1*inch, rightMargin=1*inch, topMargin=1*inch, bottomMargin=1*inch)
        styles = getSampleStyleSheet()

        # Define custom styles
        styles.add(ParagraphStyle(name='Name', fontName='Times-Roman', fontSize=14, alignment=1, spaceAfter=6))
        styles.add(ParagraphStyle(name='Contact', fontName='Times-Roman', fontSize=10, alignment=1, spaceAfter=12))
        styles.add(ParagraphStyle(name='SectionHeading', fontName='Times-Roman', fontSize=12, spaceAfter=6))
        styles.add(ParagraphStyle(name='Body', fontName='Times-Roman', fontSize=11, spaceAfter=6))
        styles.add(ParagraphStyle(name='Bullet', fontName='Times-Roman', fontSize=11, spaceAfter=6, leftIndent=0.5*inch, firstLineIndent=-0.25*inch, bulletFontName='Times-Roman', bulletFontSize=11, bulletIndent=0.25*inch))

        story = []

        # Name
        story.append(Paragraph(f"<b>{name}</b>", styles['Name']))

        # Contact Info
        contact_info = f"{email} | {phone} | {location}"
        story.append(Paragraph(contact_info, styles['Contact']))

        # Spacer
        story.append(Spacer(1, 12))

        # Add sections
        for section_name, content in sections.items():
            if content.strip():
                # Section Heading
                story.append(Paragraph(f"<b>{section_name.capitalize()}</b>", styles['SectionHeading']))

                # Section Content
                for line in content.splitlines():
                    line = line.strip()
                    if line:
                        if section_name in ["skills", "experience", "hobbies"]:  # Use bullets
                            bullet_line = f"• {line}"
                            story.append(Paragraph(bullet_line, styles['Bullet']))
                        else:
                            story.append(Paragraph(line, styles['Body']))

        try:
            doc.build(story)
        except Exception as e:
            logger.error(f"Error generating PDF file: {str(e)}")
            return jsonify({'error': f'Failed to generate PDF file: {str(e)}'}), 500
        return send_file(output_path, as_attachment=True, download_name="Fixed_Resume.pdf")

    else:
        return jsonify({'error': 'Invalid format specified'}), 400

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
                res = client.chat.completions.create(
                    model="gpt-3.5-turbo",
                    messages=[
                        {"role": "user", "content": prompt}
                    ]
                )
                summary = res.choices[0].message.content.strip()
            except Exception as e:
                logger.error(f"Error generating summary: {str(e)}")
                summary = ""

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
        logger.info(f"Saved file to {filepath}")
    except Exception as e:
        logger.error(f"Error saving file: {str(e)}")
        return jsonify({'error': f'Failed to save file: {str(e)}'}), 500

    ext = os.path.splitext(filename)[1].lower()
    try:
        if ext == '.pdf':
            resume_text = extract_text_from_pdf(filepath)
            if not resume_text:
                logger.info("Falling back to OCR for PDF text extraction")
                resume_text = extract_text_with_ocr(filepath)
        elif ext == '.docx':
            resume_text = extract_text_from_docx(filepath)
        else:
            return jsonify({'error': 'Unsupported file format'}), 400
    except Exception as e:
        logger.error(f"Error extracting text from file: {str(e)}")
        return jsonify({'error': f'Failed to extract text: {str(e)}'}), 500

    if not resume_text.strip():
        return jsonify({'error': 'Could not extract text from resume'}), 400

    name = ""
    email = ""
    phone = ""
    location = ""

    try:
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
    except Exception as e:
        logger.error(f"Error parsing resume text: {str(e)}")
        return jsonify({'error': f'Failed to parse resume text: {str(e)}'}), 500

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
        logger.error(f"Error generating cover letter: {str(e)}")
        return jsonify({'error': f'Failed to generate cover letter: {str(e)}'}), 500

@app.route('/download-cover-letter', methods=['POST'])
def download_cover_letter():
    data = request.get_json()
    cover_letter = data.get('cover_letter')

    if not cover_letter:
        return jsonify({'error': 'No cover letter provided'}), 400

    doc = Document()
    doc.add_heading("Cover Letter", level=1)
    for line in cover_letter.splitlines():
        line = line.strip()
        if line:
            doc.add_paragraph(line)

    output_path = os.path.join(app.config['UPLOAD_FOLDER'], f"cover_letter_{uuid.uuid4()}.docx")
    try:
        doc.save(output_path)
    except Exception as e:
        logger.error(f"Error saving cover letter DOCX file: {str(e)}")
        return jsonify({'error': f'Failed to save cover letter DOCX file: {str(e)}'}), 500
    return send_file(output_path, as_attachment=True, download_name="Cover_Letter.docx")

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    logger.info(f"Starting Flask app on port {port}")
    app.run(host="0.0.0.0", port=port)

logger.info("Flask app initialization complete.")
