# Changelog — NexaFarma Store

## Edición corregida — 10 de octubre de 2026

### Correcciones
- Completado el modo oscuro para formularios, tablas, tarjetas, checkout, administración y ventanas flotantes; se conserva la preferencia visual con fallback a la preferencia del sistema.
- Corregida una regla CSS residual que ocultaba los bloques de entrega, cupón y pago del checkout de una sola página.
- Eliminada la importación de Google Fonts desde CSS para evitar una dependencia de red en la primera carga.
- Corregido el cierre de sesión para usar POST + CSRF en cabecera, menú móvil y página de cuenta.
- Limitadas las URL de retorno a rutas internas seguras para evitar redirecciones abiertas.
- Guardadas las notas del checkout en el pedido.
- Validado el stock agregado por producto aunque el mismo artículo aparezca en varias líneas con diferentes personalizaciones.
- Reforzada la validación de cupones dentro de la transacción de creación del pedido.
- Evitado volver a contabilizar el mismo cupón cuando Wompi aprueba la transacción.
- Añadido bloqueo transaccional al webhook Wompi y protección frente a eventos repetidos o fuera de orden para evitar restaurar inventario dos veces.
- Añadidas transacciones bloqueantes a cambios administrativos de estado e inventario; los estados finales de pago no se reescriben mediante el formulario.
- Deshabilitada la opción Wompi hasta que existan llave pública, secreto de integridad y secreto de eventos. `.env.example` deja esas tres variables vacías.
- Reducida la información del endpoint público `/salud` para no exponer diagnósticos del proveedor de correo.
- Mejorados los estados de carga/error al validar cupones y el texto cuando un intento de Wompi ya terminó.
- Ampliado `verificar_nexafarma.py` con comprobaciones estáticas de regresión, rutas, CSRF, tema y variables de pago.

### Documentación
- Añadidos `AUDITORIA_NEXAFARMA_Y_PLAN.md` con diagnóstico, prioridades y 65 mejoras propuestas.
- Añadido `PROMPT_MAESTRO_NEXAFARMA.txt` como prompt para completar futuras fases.
- Actualizado `README.md` con las correcciones y límites reales de verificación.

### Estado de las pruebas
- Correctas: compilación Python, análisis estático de 30 plantillas Jinja, rutas `url_for`, presencia de CSRF en formularios POST y sintaxis de JavaScript con `node --check`.
- Pendientes: prueba HTTP integrada con Flask, prueba visual en navegador y pruebas reales de correo/Wompi/chatbot. En el entorno de empaquetado Flask no está instalado y no se pudo descargar por falta de conexión a Internet.


## Edición final — 11 de octubre de 2026

- Añadido el panel separado del **Agente IA de compras**, con función de tool calling y funciones validadas en servidor para catálogo, carrito, navegación y pedidos propios.
- Limitado el agente para que nunca ejecute el pago, checkout, cancelación o tareas administrativas; la acción de agregar al carrito exige intención explícita en el mensaje actual.
- Añadido fallback local al agente cuando Groq alcanza rate limit, manteniendo respuestas de respaldo diferenciadas.
- Mejorada la respuesta local del chatbot sobre ubicación y ruta real `Mi cuenta → Mis pedidos`.
- Activada la integración Gmail API por HTTPS en el código; el `.env.example` ahora incluye las variables necesarias y el proveedor `gmail_api`.
- Añadidos los avisos por correo al administrador cuando entra un pedido, confirmación al remitente del formulario de contacto y notificaciones de pago rechazado/error/anulado.
- Actualizado el panel para mostrar el estado de Gmail API y el diagnóstico del proveedor.
- Endurecido `.gitignore` para excluir JSON OAuth, tokens, copias de seguridad y sidecars SQLite.
- Retiradas de `requirements.txt` las SDK de Anthropic/Gemini que ya no se importan; chatbot y agente utilizan la API REST de Groq mediante `requests`.
- Verificado: 30 plantillas Jinja, `verificar_nexafarma.py`, sintaxis Python/JavaScript, 36 rutas GET sin HTTP 500, acciones del agente con mock y envío Gmail API con HTTP simulado. No se enviaron correos reales ni se hicieron llamadas reales a Groq durante las pruebas.

## Actualización — 16 de octubre de 2026

- Sustituida la portada inicial por una experiencia comercial centrada en un video promocional real de diez segundos, con poster, botón para pausar y diseño adaptable a móviles.
- Rediseñados los bloques de productos, categorías, personalización y argumentos de compra para dirigir con más claridad al menú.
- Ajustado el agente a un presupuesto de tokens más adecuado para GPT-OSS y corregida la correspondencia entre llamadas y respuestas de herramientas.
- Separado `AGENT_MODEL` de `CHATBOT_MODEL` para configurar y diagnosticar el agente de forma independiente.
- Añadido un flujo de ejecución directa para peticiones claras de añadir al carrito y bloqueo explícito de órdenes negadas como “no quiero agregar…”.
- La interfaz diferencia las respuestas del modelo, las acciones ejecutadas, los errores de proveedor y el modo local; los respaldos ya no se anuncian como IA conectada.
- Añadidas pruebas automatizadas simuladas para el ciclo de herramientas, errores del proveedor y acciones no autorizadas.
- Reemplazado `.env.example` por una plantilla sin credenciales reales, eliminado `.env` del paquete y limpiada la base de datos incluida para conservar el catálogo sin datos personales de clientes.
- Verificados compilación de Python, análisis de 30 plantillas Jinja, rutas/CSRF, sintaxis JavaScript, video H.264 1280×720 y escaneo de patrones de credenciales.
- Pendiente la prueba de ejecución de Flask y la llamada real a Groq: en este entorno no hay Flask/Werkzeug y no se pudo instalar por falta de conectividad. No se reutilizaron credenciales expuestas del ZIP original.
