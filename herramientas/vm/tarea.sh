F="ssh -i $HOME/.ssh/id_demo -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR cquezada@34.176.114.77"
$F 'sudo podman pull -q quay.io/minio/minio:RELEASE.2024-10-13T13-34-11Z 2>&1 | tail -1; sudo podman pull -q docker.io/chrislusf/seaweedfs:3.80 2>&1 | tail -1'
