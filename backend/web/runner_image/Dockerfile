# Lado CONFIABLE del perfil web: navegadores de la imagen oficial + el paquete de Python
# de la misma versión (la imagen oficial trae los navegadores pero no el paquete).
FROM mcr.microsoft.com/playwright/python:v1.64.1-noble
RUN pip install --no-cache-dir --break-system-packages playwright==1.64.1
