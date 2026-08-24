#!/usr/bin/env bash
# Oracle Cloud VM 을 engtutor 의 외부 진입점으로 만든다. VM 에서 한 번만 돌린다.
#
#   scp -r deploy ubuntu@<VM_IP>:~/
#   ssh ubuntu@<VM_IP>
#   sudo bash deploy/vm_setup.sh --domain engtutor.duckdns.org --email you@example.com
#
# 하는 일: nginx 설치 -> 암호 파일 생성 -> 리버스 프록시 설정 -> 방화벽 개방
#          -> Let's Encrypt 인증서 발급.
#
# 하지 않는 일: 모델을 올리지 않는다. 이 VM 에는 GPU 가 없고, 그래서 연산은
# 전부 집 GPU 에서 돈다. 여기는 요청을 넘기는 자리다.
set -euo pipefail

DOMAIN=""
EMAIL=""
USERNAME="demo"
PASSWORD=""
PORT="8501"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --domain)   DOMAIN="$2"; shift 2 ;;
        --email)    EMAIL="$2"; shift 2 ;;
        --user)     USERNAME="$2"; shift 2 ;;
        --password) PASSWORD="$2"; shift 2 ;;
        --port)     PORT="$2"; shift 2 ;;
        *) echo "모르는 옵션: $1" >&2; exit 2 ;;
    esac
done

if [[ -z "$DOMAIN" ]]; then
    cat >&2 <<'MSG'
--domain 이 필요합니다.

  암호를 한 겹 걸어도 HTTPS 가 아니면 그 암호가 평문으로 흐릅니다.
  Let's Encrypt 는 IP 주소로는 인증서를 주지 않으므로 이름이 하나 필요합니다.

  무료로 얻는 법 (예: DuckDNS)
    1. https://www.duckdns.org 에서 로그인 -> 서브도메인 생성
    2. current ip 에 이 VM 의 공인 IP 를 넣고 update
    3. --domain <이름>.duckdns.org 로 다시 실행

MSG
    exit 2
fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> 패키지 설치"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq nginx apache2-utils certbot

echo "==> 암호 파일 (/etc/nginx/engtutor.htpasswd)"
if [[ -z "$PASSWORD" ]]; then
    PASSWORD="$(head -c 12 /dev/urandom | base64 | tr -d '/+=' | head -c 12)"
    GENERATED=1
fi
htpasswd -bc /etc/nginx/engtutor.htpasswd "$USERNAME" "$PASSWORD" >/dev/null 2>&1
chmod 640 /etc/nginx/engtutor.htpasswd
chown root:www-data /etc/nginx/engtutor.htpasswd

echo "==> nginx 설정"
ln -sf /etc/nginx/sites-available/engtutor /etc/nginx/sites-enabled/engtutor
rm -f /etc/nginx/sites-enabled/default
mkdir -p /var/www/html

# 인증서가 아직 없으므로 HTTPS 블록이 들어간 최종 설정은 아직 못 올린다
# (nginx 가 없는 인증서 파일을 열지 못해 뜨지 않는다). 인증 통과용으로
# HTTP 블록만 있는 임시 설정을 먼저 올린다.
cat > /etc/nginx/sites-available/engtutor <<BOOTSTRAP
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;
    location /.well-known/acme-challenge/ { root /var/www/html; }
    location / { return 503 'engtutor: 설치 중입니다'; }
}
BOOTSTRAP
nginx -t && systemctl reload nginx

echo "==> 방화벽"
# Oracle 이미지는 VCN 보안 목록과 **별개로** 인스턴스 안에도 iptables 규칙을 넣어 둔다.
# 이걸 모르면 VCN 에서 80/443 을 열어도 계속 안 열려서 한참을 헤맨다.
if command -v netfilter-persistent >/dev/null 2>&1; then
    iptables -I INPUT 5 -p tcp --dport 80  -j ACCEPT 2>/dev/null || iptables -I INPUT -p tcp --dport 80  -j ACCEPT
    iptables -I INPUT 5 -p tcp --dport 443 -j ACCEPT 2>/dev/null || iptables -I INPUT -p tcp --dport 443 -j ACCEPT
    netfilter-persistent save >/dev/null
elif command -v firewall-cmd >/dev/null 2>&1; then
    firewall-cmd --permanent --add-service=http  >/dev/null
    firewall-cmd --permanent --add-service=https >/dev/null
    firewall-cmd --reload >/dev/null
fi

echo "==> 인증서 (Let's Encrypt)"
# --nginx 플러그인 대신 certonly --webroot 를 쓴다. 플러그인은 설정 파일을 직접
# 고쳐 놓는데, 그러면 바로 다음 줄에서 우리 설정으로 덮어쓸 때 무엇이 남고
# 무엇이 사라졌는지 알 수 없게 된다. 인증서만 받아 오고 설정은 우리가 쓴다.
CERTBOT_ARGS=(certonly --webroot -w /var/www/html -d "$DOMAIN"
              --non-interactive --agree-tos
              --deploy-hook "systemctl reload nginx")
if [[ -n "$EMAIL" ]]; then CERTBOT_ARGS+=(-m "$EMAIL"); else CERTBOT_ARGS+=(--register-unsafely-without-email); fi
certbot "${CERTBOT_ARGS[@]}"

echo "==> 최종 설정 적용"
sed -e "s/__DOMAIN__/$DOMAIN/g" -e "s/__PORT__/$PORT/g" \
    "$HERE/nginx/engtutor.conf" > /etc/nginx/sites-available/engtutor
nginx -t && systemctl reload nginx
systemctl enable nginx >/dev/null

cat <<MSG

============================================================
  준비됐습니다.

  주소   https://$DOMAIN
  아이디 $USERNAME
  암호   $PASSWORD${GENERATED:+   (자동 생성 — 지금 적어 두세요)}

  다음은 집 PC 에서:
    .env 에 TUNNEL_HOST=$DOMAIN 을 넣고
    docker compose --profile expose up -d tunnel

  아직 남은 일: Oracle 콘솔의 VCN 보안 목록에서 80/443 인그레스를 여세요.
  (인스턴스 안 iptables 는 이 스크립트가 열었습니다)
============================================================
MSG
