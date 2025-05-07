from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from werkzeug.utils import secure_filename
import os
import uuid
import json
import re
from openai import OpenAI
from docx import Document

app = Flask(__name__, static_url_path='/static')
CORS(app, resources={r"/*": {"origins": "https://resumefixerpro.com"}})

UPLOAD_FOLDER = 'uploads'
STATIC_FOLDER = 'static'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(STATIC_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

def generate_ai_resume_content(data):
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

    # Create dynamic AI content if fields are empty
    prompt_parts = []
    if not summary:
        prompt_parts.append("Generate a 2-3 line professional summary for a resume.")
    if not experience:
        prompt_parts.append("Write 2-3 bullet points for professional experience suitable for a fresher.")
    if not skills:
        prompt_parts.append("List 5-6 technical and soft skills relevant for a resume.")
    if not certifications:
        prompt_parts.append("List 2-3 sample certifications.")
    if not hobbies:
        prompt_parts.append("List 2-3 hobbies for resume.")

    ai_generated = {}
    if prompt_parts:
        full_prompt = "\n".join(prompt_parts)
        try:
            response = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": "You are a helpful assistant that creates clean, short resume content."},
                    {"role": "user", "content": full_prompt}
                ]
            )
            output = response.choices[0].message.content.strip()
            outputs = output.split("\n\n")
            idx = 0
            if not summary:
                ai_generated['summary'] = outputs[idx].strip() if idx < len(outputs) else ""
                idx += 1
            if not experience:
                ai_generated['experience'] = outputs[idx].strip() if idx < len(outputs) else ""
                idx += 1
            if not skills:
                ai_generated['skills'] = outputs[idx].strip() if idx < len(outputs) else ""
                idx += 1
            if not certifications:
                ai_generated['certifications'] = outputs[idx].strip() if idx < len(outputs) else ""
                idx += 1
            if not hobbies:
                ai_generated['hobbies'] = outputs[idx].strip() if idx < len(outputs) else ""
        except Exception as e:
            pass

    def format_section(title, content):
        if content.strip():
            safe_content = content.replace("\n", "<br>")
            return f"""
            <div class=\"section\">
              <h3 style='font-size:0.95rem; line-height:1.3; color:#222; margin-bottom:4px; border-bottom:1px solid #ccc;'>{title}</h3>
              <div>{safe_content}</div>
            </div>
            """
        return ""

    sections = []

    if name or email or phone or location:
        sections.append(f"""
        <div style=\"text-align:center; margin-bottom: 1.5rem;\">
            <div style=\"font-size: 1.5rem; font-weight: bold; color: #1D75E5;\">{name}</div>
            <div style=\"font-size: 0.95rem; color: #444;\">{email}<br>{phone}<br>{location}</div>
        </div>
        """)

    sections.append(format_section("Summary", summary or ai_generated.get('summary', '')))
    sections.append(format_section("Education", education))
    sections.append(format_section("Experience", experience or ai_generated.get('experience', '')))
    sections.append(format_section("Skills", skills or ai_generated.get('skills', '')))
    sections.append(format_section("Certifications", certifications or ai_generated.get('certifications', '')))
    sections.append(format_section("Languages", languages))
    sections.append(format_section("Hobbies", hobbies or ai_generated.get('hobbies', '')))

    resume_html = "\n".join(sections)
    return {
        "success": True,
        "html": resume_html
    }

@app.route('/generate-ai-resume', methods=['POST'])
def generate_ai_resume():
    try:
        data = request.json
        result = generate_ai_resume_content(data)
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
