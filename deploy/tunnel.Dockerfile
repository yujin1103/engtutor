# 집 GPU 를 바깥에 내보내는 터널의 집 쪽 끝.
#
# 이 컨테이너는 연산을 하지 않는다. compose 네트워크 안의 ui:8501 을
# Oracle VM 의 127.0.0.1:8501 에 붙여 놓기만 한다.
#
# autossh 를 쓰는 이유: 집 회선이 끊기거나 VM 이 재부팅되면 ssh 는 조용히 죽는다.
# 그러면 시범 URL 이 502 가 되고, 아무도 안 알려 준다.
FROM alpine:3.20

RUN apk add --no-cache autossh openssh-client

# 키는 실행 시점에 /keys 로 마운트한다. 이미지에 넣지 않는다.
ENTRYPOINT ["/bin/sh", "-c"]
