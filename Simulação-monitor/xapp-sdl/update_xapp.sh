#!/bin/bash

namespace="ricxapp"
xapp_name="xappsdl"

echo "namespace=$namespace"
echo "xapp_name=$xapp_name"
echo " "

chave="Falha"
saida_python=$(python3 /home/openran-br/xapp-workshop/exercise-xapps/xapp-2-sdl/Check_assinatura.py)

if [[ "$saida_python" == *"$chave"* ]]; then
    echo "A integridade do xapp está comprometida, Não será implementado."
    exit
else
    echo "Xapp autentico e integro, começando a implementação."
fi


echo "----------------- Onboarding the xApp chart -----------------"
dms_cli onboard init/config-file.json init/schema.json

echo "----------------- Terminating xApp pod -----------------"
dms_cli uninstall $xapp_name $namespace

echo -n "Waiting pod termination"
while kubectl get pods -n $namespace | grep -q $namespace-$xapp_name-
do
    sleep 1 # seconds
    echo -n "."
done

printf "\n"
echo "----------------- Removing previous image -----------------"
docker image rm 127.0.0.1:5001/$xapp_name:1.0.0


echo "----------------- Building new image -----------------"
docker build . -t 127.0.0.1:5001/$xapp_name:1.0.0 --network host --security-opt=no-new-privileges

echo "----------------- Pushing new image -----------------"
docker push 127.0.0.1:5001/$xapp_name:1.0.0

echo "----------------- Installing the xApp -----------------"
dms_cli install $xapp_name 1.0.0 $namespace

echo -n "Waiting pod creation"
while ! kubectl get pods -n $namespace | grep $namespace-$xapp_name- | grep -q "1/1";
do
    if kubectl get pods -n $namespace | grep $namespace-$xapp_name- | grep -q CrashLoopBackOff; then 
        printf "\n%s" "INSTALLATION ERROR: CrashLoopBackOff"
        break
    fi
    sleep 1 # seconds
    echo -n .
done

printf "\n"

echo "----------------- Getting pod's logs -----------------"
sleep 1
kubectl logs POD/$(kubectl get pods -n $namespace | grep $namespace-$xapp_name- | awk '{print $1}') -n $namespace | tail -n 3