# Correo transaccional de NexaFarma

La app contiene plantillas HTML de la marca NexaFarma para registro, restablecimiento de contraseña, avisos de pedidos, pagos, mensajes de contacto y comprobantes. El código contempla Gmail API, SMTP y Resend. No hay credenciales de correo incluidas, y no se ha enviado un mensaje real desde este paquete.

## Opción recomendada: Gmail API por HTTPS

1. En Google Cloud crea un proyecto que controles y habilita Gmail API.
2. Configura OAuth para el remitente, con el alcance de envío `https://www.googleapis.com/auth/gmail.send`, siguiendo la guía vigente de Google.
3. Obtén un refresh token para la misma cuenta remitente. Nunca lo compartas ni lo incluyas en el ZIP o en Git.
4. Define en `.env`:

```dotenv
EMAIL_PROVIDER=gmail_api
GMAIL_API_CLIENT_ID=valor_privado
GMAIL_API_CLIENT_SECRET=valor_privado
GMAIL_API_REFRESH_TOKEN=valor_privado
GMAIL_API_FROM=tu-cuenta@gmail.com
GMAIL_API_REPLY_TO=tu-cuenta@gmail.com
MAIL_ADMIN=tu-cuenta@gmail.com
```

5. Inicia la app, abre Administración → Configuración y revisa el diagnóstico. Envía un correo de prueba a una dirección que controles.

## Alternativas

- SMTP: `EMAIL_PROVIDER=smtp`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_USE_TLS` y `SMTP_USE_SSL`.
- Resend: `EMAIL_PROVIDER=resend`, `RESEND_API_KEY` y `RESEND_FROM`. El dominio remitente debe estar verificado en tu cuenta del proveedor.

Usa solo una opción principal y configura `BASE_URL` para que los enlaces de los correos apunten al sitio correcto. Una prueba exitosa significa que el proveedor aceptó el envío; revisa igualmente bandeja de entrada, correo no deseado y dominio autenticado (SPF/DKIM/DMARC). No es posible garantizar que los correos nunca lleguen a Spam.
