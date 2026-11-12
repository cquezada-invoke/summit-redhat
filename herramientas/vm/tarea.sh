#!/bin/bash
# Borra ramas vmt/* que solo despliegan FaceOps (las reemplaza faceops-autodeploy.timer). Solo si la rama sigue en el SHA revisado.
cd "${GITHUB_WORKSPACE:-.}" || exit 1
echo "cola antes=$(git ls-remote origin 'refs/heads/vmt/*' | wc -l)"
git ls-remote origin 'refs/heads/vmt/*' > /tmp/ad-ramas.txt
cat > /tmp/ad-lista.txt <<'L'
7b3f60cbc1d8db1dece31d7153684d096de3c5a0 refs/heads/vmt/c-144704-5746
7ffb596d72f75855e2ac126b7cfa471d391275fc refs/heads/vmt/c-223444-21760
2c73964ee1c7fda26714d3d8bda478a061fc5ee5 refs/heads/vmt/c-230713-26588
daacf2cf08c9c2363056c8e82ccc8bfbcce84fdd refs/heads/vmt/l-000411-11620
e1d4391bf8b7ba1eb1f00ba7a31ae9248dc2f5db refs/heads/vmt/l-002111-6180
97af39701f3a436ffa3d025fdff68c1f8bffcc8b refs/heads/vmt/l-002717-15395
237cf5c440cee47a8453588624eb10bc28930390 refs/heads/vmt/l-003245-22642
608bd25c0dad435b84d56536e94673b65275f514 refs/heads/vmt/l-003737-28421
23da38719d951e477f83cb2289618e7247462936 refs/heads/vmt/l-221026-11743
0d8618f5132deb3a28e195856f223478ef2a633b refs/heads/vmt/l-221622-23810
ab1888da53ae09f251480bf98bd3041c7894173e refs/heads/vmt/l-221833-28088
149d549aae7ba0804a34e6dc89877b9df8464b0a refs/heads/vmt/l-222511-7619
68c432d72a9dc9c00d648104d1018a66f9f1a8bd refs/heads/vmt/l-224207-32537
5ac6e7d5eb78abf59108a8078cde66f0cee3a885 refs/heads/vmt/l-225147-11420
537dabaf9c1804b6c869033c96cd08284f861a5e refs/heads/vmt/l-225218-12042
78b654d5c6def2da7f5c5a768765c6e9012144fc refs/heads/vmt/l-232020-7836
3f70a43ac2b7bf533f1983a50335c7675c1c9237 refs/heads/vmt/l-232319-10974
b07372235d6c36a69f2b2883fd64b336465ccdea refs/heads/vmt/l-232416-12240
f596cc1b5d738d33702e5f71ec314dfd3d95eed2 refs/heads/vmt/l-232526-13979
b983e7f0046d80c3f7b376498fc84b496dad5f95 refs/heads/vmt/l-234520-12494
23bf25a608045ac6ccce7cf6e6ab51f8f3c65c23 refs/heads/vmt/l-234550-13449
da9f685fc1205e1c1ee22b01181717797eb1e36c refs/heads/vmt/l-235029-21118
cd1ef6ea8afb5a9c67c915778750414ffcdf6e83 refs/heads/vmt/l-235652-32273
eaa6df3384adfe5c7876dda3ef4cd5e06583029c refs/heads/vmt/l-232843-18722
a093c1876dbe93569ffe8c90fec314105b290a80 refs/heads/vmt/l-233727-31910
ee0160a59c77109ab5cf65e5f0004367e6047d6e refs/heads/vmt/l-235914-3753
08bb0153e5f150d62d830a9b9b06311286da39f6 refs/heads/vmt/l-233013-21070
01a72a6f7ef3f010aeeff6bd1f23a0295d02323f refs/heads/vmt/l-234021-3946
L
awk 'NR==FNR{r[$2]=$1;next} (r[$2]==$1){print ":"$2}' /tmp/ad-ramas.txt /tmp/ad-lista.txt > /tmp/ad-borrar.txt
echo "a borrar=$(wc -l < /tmp/ad-borrar.txt) de $(wc -l < /tmp/ad-lista.txt) revisadas"
r=1; for i in 1 2 3; do timeout 120 git push -q origin $(cat /tmp/ad-borrar.txt) >/tmp/ad-push.log 2>&1 && { r=0; break; }; sleep 3; done
[ $r = 0 ] && echo "borradas=$(wc -l < /tmp/ad-borrar.txt)" || grep -v -i -E "token|http|auth" /tmp/ad-push.log | head -2
echo "cola despues=$(git ls-remote origin 'refs/heads/vmt/*' | wc -l)"
rm -f /tmp/ad-*.txt /tmp/ad-push.log
