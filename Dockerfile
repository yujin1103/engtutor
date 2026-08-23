FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /workspace

# uv로 설치한다. 의존성이 바뀌어 캐시가 깨져도 재빌드가 수십 초 -> 수 초.
RUN pip install --no-cache-dir uv

COPY requirements.txt .
RUN uv pip install --system --no-cache -r requirements.txt

# 실제 소스는 compose에서 bind mount로 덮어쓴다.
# 여기서 COPY 해두는 건 compose 없이 이미지 단독 실행할 때를 위한 것.
COPY . .

EXPOSE 8000 8501
