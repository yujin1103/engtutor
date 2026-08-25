FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /workspace

# uv로 설치한다. 의존성이 바뀌어 캐시가 깨져도 재빌드가 수십 초 -> 수 초.
RUN pip install --no-cache-dir uv

COPY requirements.txt .
RUN uv pip install --system --no-cache -r requirements.txt

# WordNet 코퍼스를 이미지에 굽는다. nltk 의 기본 검색 경로 중 하나라 코드에서
# 경로를 지정할 필요가 없다. 런타임 다운로드로 두면 오프라인 환경에서 어휘 검사가
# 조용히 꺼지고, 그러면 검사가 없는 줄도 모른 채 지나간다.
RUN python -m nltk.downloader -d /usr/local/share/nltk_data wordnet

# 실제 소스는 compose에서 bind mount로 덮어쓴다.
# 여기서 COPY 해두는 건 compose 없이 이미지 단독 실행할 때를 위한 것.
COPY . .

EXPOSE 8000 8501
