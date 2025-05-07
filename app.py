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

    prompt = f"""
You are a professional resume writer. Using the following user details, generate clean, concise, and professional resume sections in HTML format.
Use proper section titles like Summary, Education, Experience, Skills, Certifications, Languages, and Hobbies.
Format all <h3> headings with inline style: font-size:0.95rem; line-height:1.3; color:#222; margin-bottom:4px; border-bottom:1px solid #ccc;
Use <div> tags for content. Use <br> for line breaks. Only return HTML string.

User Info:
Name: {name}
Email: {email}
Phone: {phone}
Location: {location}
Education: {education}
Experience: {experience}
Skills: {skills}
Certifications: {certifications}
Languages: {languages}
Hobbies: {hobbies}
    """

    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You generate structured HTML resumes."},
                {"role": "user", "content": prompt}
            ]
        )
        html = response.choices[0].message.content.strip()
        return {"success": True, "html": html}
    except Exception as e:
        return {"success": False, "error": str(e)}

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
