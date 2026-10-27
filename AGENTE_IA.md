# Asistente de NexaFarma

El asistente sirve para explicar la navegación de la tienda, consultar datos del catálogo general, ayudar con el carrito, explicar pagos y orientar sobre el historial de pedidos de la cuenta actual.

## Datos y herramientas

- Busca únicamente productos activos con existencias que no estén marcados como sujetos a fórmula.
- Puede mostrar precios e inventario leídos de la base actual.
- Puede agregar o editar el carrito si el visitante da una instrucción explícita, después de validar producto y stock en el backend.
- Puede consultar el carrito, datos básicos de tienda, navegación y pedidos de la sesión iniciada.
- No crea pedidos, no confirma pagos, no cancela compras ni realiza tareas administrativas.

## Seguridad sanitaria

NexaFarma es una marca ficticia para una demo. El asistente no diagnostica, no recomienda medicamentos, no calcula dosis ni plantea tratamientos o interacciones. Las consultas clínicas se bloquean antes de llamar al modelo generativo. Debe remitir a un médico o farmacéutico habilitado; ante una urgencia, a servicios locales de emergencia. Los productos marcados como sujetos a fórmula no se pueden agregar al carrito mediante el sitio ni el asistente.

## Modo local y modelo generativo

Sin `GROQ_API_KEY`, el agente utiliza respuestas locales limitadas. Para activar el modelo, configura una clave propia, revisa el nombre de modelo en `AGENT_MODEL` y confirma cuota y permisos de tool-calling del proveedor. La clave no se escribe en el código. El estado “IA configurada” solo indica que hay una variable en el entorno; no certifica que el proveedor esté disponible en todo momento.

## Pruebas

Prueba consultas de catálogo, precio, pedido y ayuda. Verifica que una pregunta médica responda con el aviso sanitario sin llamar a Groq y que el agente no modifique el carrito por una simple recomendación. Los datos de la sesión deben ser los del cliente autenticado y no de otra cuenta.
