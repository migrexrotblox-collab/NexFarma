# Puesta en marcha y producción de NexaFarma

## 1. Entorno

- Usa una versión compatible y mantenida de Python (se recomienda 3.11 o 3.12).
- Instala `requirements.txt` dentro de un entorno virtual.
- Configura `.env` fuera del control de versiones. Define `SECRET_KEY`, `BASE_URL`, credenciales administrativas propias y datos de contacto verificados.
- Configura HTTPS en el hosting y establece `SESSION_COOKIE_SECURE=1` en producción.
- Revisa el registro de eventos y la política de copias de seguridad de SQLite y `static/uploads`.

## 2. Servicios externos

- Google OAuth: registra el callback real y conserva el ID y secreto en variables de entorno.
- Correo: elige Gmail API, SMTP o Resend; configura remitente y dominio y envía una prueba antes de usarlo.
- Mercado Pago: configura Access Token y secreto de notificación, verifica la firma del webhook y usa `BASE_URL` público HTTPS.
- Wompi: usa claves del ambiente de pruebas y verifica cálculo de integridad, URL de eventos, resultados y estados de pago. Mantén `WOMPI_TEMPORARILY_UNAVAILABLE=1` hasta pasar la prueba de punta a punta; luego decide con conocimiento del comercio si cambia a `0`.
- Groq: es opcional. Usa una clave propia, límites de uso y un modelo compatible con las herramientas que invoca la aplicación.

No se incluye ninguna credencial ni se afirma que estos servicios están conectados. Ningún pago real debe habilitarse hasta verificar el flujo completo en el entorno del comercio.

## 3. Checklist previo a publicar

- Cambiar cualquier dato ficticio, teléfono, ciudad o dirección genérica por información real y autorizada.
- Completar política de privacidad, condiciones de venta, entrega, devoluciones, tratamiento de datos y datos legales aplicables.
- Revisar las obligaciones sanitarias de los artículos que se vayan a comercializar. La demo bloquea productos marcados como sujetos a fórmula, pero no cuenta con sistema de receta electrónica ni flujo de dispensación.
- Probar registro, login, recuperación, CSRF, creación de pedido, cálculos, cupones, stock, correo y permisos de cada rol.
- Probar webhooks con eventos falsos controlados y con credenciales de sandbox; verificar que firmas/importe/moneda/referencia coincidan.
- Usar datos de prueba, proteger logs y no registrar contraseñas, tarjetas, tokens ni información médica sensible.
- Configurar copias de seguridad y procedimiento para restaurar la base.

## 4. Arranque local

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python app.py
```

Abre `http://127.0.0.1:5000`. El catálogo de muestra se inserta al crear una base nueva.
