#!/bin/bash
curl -sfL https://get.k3s.io | INSTALL_K3S_EXEC="--write-kubeconfig-mode 644 --node-ip 192.168.50.1 --node-external-ip 192.168.11.64 --flannel-iface=eth0 --token EvPpHp6pic4KDn5titE4q0qcqiRE/iDwECorNnAegBk=" sh -
mkdir -p /home/pi/.kube
cp /etc/rancher/k3s/k3s.yaml /home/pi/.kube/config
chown -R pi:pi /home/pi/
