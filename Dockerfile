FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py .
COPY index.html .
ENV SUPABASE_URL="https://obnhesobzgppiidigdtu.supabase.co"
ENV SUPABASE_KEY="sb_publishable_-zpPTE45VhRROAZOV0xxFg_iTMVSYLA"
ENV JWT_SECRET="change-this-secret-in-production"
ENV AUTH_EMAIL="library77777@yopmail.com"
ENV AUTH_PASSWORD="library@12345"
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
