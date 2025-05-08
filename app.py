from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from werkzeug.utils import secure_filename
import os
import uuid
import json
import re
from docx import Document
import openai
from resume_ai_analyzer import (
    analyze_resume_with_openai,
    extract_text_from_pdf,
    extract_text_from_docx,
    extract_text_with_ocr,
    check_ats_compatibility
)

openai.api_key = os.environ.get("OPENAI_API_KEY")

app = Flask(__name__, static_url_path='/static')
CORS(app, resources={r"/*": {"origins": "https://resumefixerpro.com"}})

UPLOAD_FOLDER = 'uploads'
STATIC_FOLDER = 'static'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(STATIC_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

@app.route('/upload', methods=['POST'])
def upload_resume():
    file = request.files.get('file')
    atsfix = request.form.get('atsfix') == 'true'
    if not file or file.filename == '':
        return jsonify({'error': 'No file uploaded'}), 400

    filepath = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(file.filename))
    file.save(filepath)

    try:
        result = analyze_resume_with_openai(filepath, atsfix=atsfix)

        ext = os.path.splitext(filepath)[1].lower()
        resume_text = ""
        if ext == ".pdf":
            resume_text = extract_text_from_pdf(filepath) or extract_text_with_ocr(filepath)
        elif ext == ".docx":
            resume_text = extract_text_from_docx(filepath)

        def extract_section(text, keyword):
            pattern = rf"{keyword}[:\-]?\s*(.*?)(\n\n|\Z)"
            match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
            return match.group(1).strip() if match else ""

        sections = {
            "skills": extract_section(resume_text, "skills"),
            "experience": extract_section(resume_text, "experience"),
            "education": extract_section(resume_text, "education"),
            "certifications": extract_section(resume_text, "certifications"),
            "languages": extract_section(resume_text, "languages"),
            "hobbies": extract_section(resume_text, "hobbies"),
        }

        result["sections"] = sections
        return jsonify(result)

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/resume-score', methods=['POST'])
def resume_score():
    file = request.files.get('file')
    if not file:
        return jsonify({'error': 'No file uploaded'}), 400
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(file.filename))
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
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a strict but fair resume scoring assistant."},
                {"role": "user", "content": prompt}
            ]
        )
        score_raw = response.choices[0].message.content.strip()
        score = int(''.join(filter(str.isdigit, score_raw)))
        return jsonify({"score": max(0, min(score, 100))})
    except:
        return jsonify({"score": 70})

@app.route('/check-ats', methods=['POST'])
def check_ats():
    file = request.files.get('file')
    if not file:
        return jsonify({'error': 'No file uploaded'}), 400
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(file.filename))
    file.save(filepath)
    try:
        ats_result = check_ats_compatibility(filepath)
        return jsonify({'ats_report': ats_result})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/generate-suggestions', methods=['POST'])
def generate_suggestions():
    try:
        file = request.files["file"]
        temp_path = os.path.join("temp", file.filename)
        os.makedirs("temp", exist_ok=True)
        file.save(temp_path)

        result = analyze_resume_with_openai(temp_path)
        os.remove(temp_path)
        return jsonify(result)
    except Exception as e:
        print("❌ [OpenAI ERROR - /generate-suggestions]", str(e))
        return jsonify({"error": "Failed to generate suggestions."})

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
                res = openai.ChatCompletion.create(
                    model="gpt-3.5-turbo",
                    messages=[
                        {"role": "user", "content": prompt}
                    ]
                )
                return res.choices[0].message.content.strip()
            except:
                return user_input

        if summary.strip():
            summary = generate_section_content("summary", summary)
        else:
            summary = generate_section_content("summary", "")

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
        return jsonify({'success': False, 'error': str(e)})
@app.route('/parse-resume', methods=['POST'])
def parse_resume_legacy():
    return upload_resume()

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
