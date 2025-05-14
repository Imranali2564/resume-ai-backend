@echo off
cd /d %~dp0styles
echo Running Tailwind CSS build...
npx tailwindcss -i input.css -o ../output.css --minify
echo Done! Press any key to exit.
pause >nul
