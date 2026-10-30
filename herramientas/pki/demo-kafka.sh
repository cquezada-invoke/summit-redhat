#!/bin/bash
# Demo de la skill «certificados»: Kafka (Apache 3.9, modo KRaft, un nodo) en un equipo del laboratorio, con:
#   · listener SSL en 9093 que lee su keystore de /etc/pki/faceops/kafka/keystore.p12 (lo deja la skill)
#   · la clave del keystore NO va en server.properties: Kafka la lee de keystore.properties (FileConfigProvider)
#   · cliente de prueba (kafka-prueba-cliente) cuyo truststore tiene SOLO la CA interna
# Kafka no arranca hasta que la skill emite el certificado (su comando de recarga es «systemctl restart kafka»).
# Uso: demo-kafka.sh [equipo-libvirt]   (por defecto nodo-nginx). Idempotente.
set -euo pipefail
VM=${1:-nodo-nginx}; KV=3.9.1; J=17.0.13_11
D=~/faceops/descargas; mkdir -p $D
IP=$(sudo virsh domifaddr $VM | awk '/ipv4/{print $4}' | cut -d/ -f1); CN=$VM.demo.local
K=$(mktemp); trap 'rm -f $K' EXIT; sudo podman exec faceops cat /var/lib/faceops/llaves/id_demo > $K; chmod 600 $K
S="ssh -i $K -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR ansible@$IP"
echo "== Descargas (en el anfitrión; el equipo no tiene internet)"
[ -s $D/kafka_2.13-$KV.tgz ] || curl -sfL -o $D/kafka_2.13-$KV.tgz https://archive.apache.org/dist/kafka/$KV/kafka_2.13-$KV.tgz
[ -s $D/jre17.tgz ] || curl -sfL -o $D/jre17.tgz "https://github.com/adoptium/temurin17-binaries/releases/download/jdk-${J/_/%2B}/OpenJDK17U-jre_x64_linux_hotspot_$J.tar.gz"
ls -la $D | tail -2
echo "== Copiar e instalar en $VM ($IP)"
scp -q -i $K -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR \
  $D/kafka_2.13-$KV.tgz $D/jre17.tgz ~/pki/ca.crt ansible@$IP:/tmp/
$S "sudo bash -s" <<R
set -e
id kafka >/dev/null 2>&1 || useradd -r -m -d /var/lib/kafka -s /sbin/nologin kafka
[ -x /opt/java/bin/java ] || { mkdir -p /opt/java && tar xzf /tmp/jre17.tgz -C /opt/java --strip-components=1; }
[ -d /opt/kafka/bin ] || { mkdir -p /opt/kafka && tar xzf /tmp/kafka_2.13-$KV.tgz -C /opt/kafka --strip-components=1; }
grep -q " $CN" /etc/hosts || echo "$IP $CN" >> /etc/hosts
mkdir -p /etc/kafka /var/lib/kafka/datos /etc/pki/faceops/clientes
install -m 0644 /tmp/ca.crt /etc/pki/faceops/clientes/ca-interna.crt
cat > /etc/kafka/server.properties <<C
process.roles=broker,controller
node.id=1
controller.quorum.voters=1@127.0.0.1:9094
listeners=PLAINTEXT://127.0.0.1:9092,SSL://0.0.0.0:9093,CONTROLLER://127.0.0.1:9094
advertised.listeners=PLAINTEXT://127.0.0.1:9092,SSL://$CN:9093
controller.listener.names=CONTROLLER
listener.security.protocol.map=PLAINTEXT:PLAINTEXT,SSL:SSL,CONTROLLER:PLAINTEXT
inter.broker.listener.name=PLAINTEXT
log.dirs=/var/lib/kafka/datos
num.partitions=1
offsets.topic.replication.factor=1
transaction.state.log.replication.factor=1
transaction.state.log.min.isr=1
# Certificado: lo deja y lo renueva FaceOps (skill «certificados»). La clave del keystore se lee de un archivo aparte.
config.providers=file
config.providers.file.class=org.apache.kafka.common.config.provider.FileConfigProvider
ssl.keystore.type=PKCS12
ssl.keystore.location=/etc/pki/faceops/kafka/keystore.p12
ssl.keystore.password=\\\${file:/etc/pki/faceops/kafka/keystore.properties:clave}
ssl.key.password=\\\${file:/etc/pki/faceops/kafka/keystore.properties:clave}
C
export JAVA_HOME=/opt/java PATH=/opt/java/bin:\$PATH
grep -vE '^(ssl|config\.providers)' /etc/kafka/server.properties > /tmp/formato.properties   # el formato no necesita el keystore
[ -f /var/lib/kafka/datos/meta.properties ] || /opt/kafka/bin/kafka-storage.sh format -t \$(/opt/kafka/bin/kafka-storage.sh random-uuid) -c /tmp/formato.properties >/dev/null
rm -f /tmp/formato.properties
chown -R kafka:kafka /var/lib/kafka /etc/kafka
cat > /etc/systemd/system/kafka.service <<U
[Unit]
Description=Apache Kafka (demo FaceOps · certificados de la CA interna)
After=network-online.target
ConditionPathExists=/etc/pki/faceops/kafka/keystore.p12
[Service]
User=kafka
Environment=JAVA_HOME=/opt/java
Environment=PATH=/opt/java/bin:/usr/bin:/bin
Environment="KAFKA_HEAP_OPTS=-Xmx384m -Xms384m"
ExecStart=/opt/kafka/bin/kafka-server-start.sh /etc/kafka/server.properties
Restart=on-failure
[Install]
WantedBy=multi-user.target
U
cat > /usr/local/bin/kafka-prueba-cliente <<'X'
#!/bin/bash
# Cliente de prueba: confía SOLO en la CA interna (no en el certificado del servidor) y valida el nombre del servidor.
export JAVA_HOME=/opt/java PATH=/opt/java/bin:\$PATH KAFKA_HEAP_OPTS=-Xmx128m
P=\$(mktemp); trap 'rm -f \$P' EXIT
printf 'security.protocol=SSL\nssl.truststore.type=PEM\nssl.truststore.location=/etc/pki/faceops/clientes/ca-interna.crt\nssl.endpoint.identification.algorithm=https\n' > \$P
B=$CN:9093; M="hola desde FaceOps \$(date +%H:%M:%S)"
echo "\$M" | /opt/kafka/bin/kafka-console-producer.sh --bootstrap-server \$B --topic prueba-tls --producer.config \$P 2>&1 | grep -v WARN || true
/opt/kafka/bin/kafka-console-consumer.sh --bootstrap-server \$B --topic prueba-tls --consumer.config \$P --from-beginning --timeout-ms 15000 2>/dev/null | tail -1
echo | openssl s_client -connect \$B -servername $CN 2>/dev/null | openssl x509 -noout -serial -enddate | tr '\n' ' '; echo
X
chmod 755 /usr/local/bin/kafka-prueba-cliente
systemctl daemon-reload; systemctl enable -q kafka
systemctl is-active -q firewalld && { firewall-cmd -q --query-port=9093/tcp || { firewall-cmd -q --add-port=9093/tcp; firewall-cmd -q --permanent --add-port=9093/tcp; }; } || true
rm -f /tmp/kafka_2.13-$KV.tgz /tmp/jre17.tgz /tmp/ca.crt
echo "Kafka instalado en $CN (arranca cuando FaceOps emita su certificado)"
R
