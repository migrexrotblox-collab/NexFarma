(function () {
  const csrf = () => document.querySelector('meta[name="csrf-token"]')?.content || '';

  const statusFor = (data) => {
    switch (data.mode) {
      case 'agent-ai': return 'IA conectada · respuesta del modelo';
      case 'agent-ai-tools': return 'IA conectada · datos consultados';
      case 'agent-action': return 'Acción validada · revisa el carrito';
      case 'agent-action-failed': return 'No se modificó el carrito';
      case 'agent-no-action': return 'Sin cambios';
      case 'agent-health-safety': return 'Orientación sanitaria bloqueada por seguridad';
      case 'agent-local-fallback': return 'Ayuda local de respaldo';
      case 'agent-local-only': return 'Modo local · la IA externa es opcional';
      case 'agent-error': return 'Error interno · revisa el servidor';
      default: return 'Asistente disponible';
    }
  };

  function initAgent(options) {
    const { toggle, panel, close, reset, messages, quick, form, input, status, initiallyOpen = false } = options;
    if (!panel || !messages || !form || !input || !status) return;
    let opened = initiallyOpen;
    let greeted = false;
    let sending = false;

    const setOpen = (value) => {
      opened = Boolean(value);
      if (panel.hasAttribute('hidden') || panel.hidden !== undefined) panel.hidden = !opened;
      panel.classList.toggle('hidden', !opened);
      toggle?.setAttribute('aria-expanded', String(opened));
      if (opened) {
        greet();
        input.focus();
      }
    };

    const scroll = () => { messages.scrollTop = messages.scrollHeight; };
    const add = (text, who) => {
      const el = document.createElement('div');
      el.className = 'agent-msg ' + who;
      el.textContent = String(text || '');
      messages.appendChild(el);
      scroll();
      return el;
    };
    const typing = () => {
      const el = add('Estoy revisando tu consulta…', 'bot');
      el.style.opacity = '.65';
      el.setAttribute('aria-label', 'El asistente está procesando la consulta');
      return el;
    };
    const greet = () => {
      if (greeted) return;
      greeted = true;
      add('¡Hola! Soy el asistente de NexaFarma. Puedo ayudarte a navegar el catálogo general, consultar precios publicados, revisar el carrito, entender los pagos y encontrar tus pedidos. No doy diagnósticos, recomendaciones de medicamentos ni dosis. NexaFarma es una tienda ficticia de demostración.', 'bot');
    };

    async function send(rawText) {
      const text = String(rawText || '').trim();
      if (!text || sending) return;
      sending = true;
      const sendButton = form.querySelector('button[type="submit"]');
      if (sendButton) sendButton.disabled = true;
      input.disabled = true;
      add(text, 'user');
      input.value = '';
      quick?.classList.add('hidden');
      const working = typing();
      status.textContent = 'Conectando con el asistente…';
      try {
        const response = await fetch('/api/agent', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf(), 'X-Requested-With': 'XMLHttpRequest' },
          body: JSON.stringify({ message: text })
        });
        const data = await response.json().catch(() => ({}));
        working.remove();
        if (!response.ok) {
          status.textContent = 'El servidor no pudo completar la consulta';
          add(data.reply || data.error || 'No pude procesar el mensaje. Inténtalo de nuevo.', 'bot');
          return;
        }
        status.textContent = statusFor(data);
        add(data.reply || 'No recibí una respuesta completa. Inténtalo de nuevo.', 'bot');
        if (data.ai_error) add('Aviso: el proveedor de IA no respondió y se utilizó la ayuda local. Revisa GROQ_API_KEY solo si deseas activar esta integración.', 'bot');
      } catch (_) {
        working.remove();
        status.textContent = 'Sin conexión con el servidor';
        add('No pude conectar con NexaFarma. Comprueba que la aplicación esté iniciada e inténtalo otra vez.', 'bot');
      } finally {
        sending = false;
        input.disabled = false;
        if (sendButton) sendButton.disabled = false;
        input.focus();
      }
    }

    toggle?.addEventListener('click', () => setOpen(!opened));
    close?.addEventListener('click', () => { setOpen(false); toggle?.focus(); });
    reset?.addEventListener('click', async () => {
      if (sending) return;
      try {
        await fetch('/api/agent/reset', { method: 'POST', headers: { 'X-CSRFToken': csrf(), 'X-Requested-With': 'XMLHttpRequest' } });
      } catch (_) { /* Se puede limpiar la conversación visible aunque falle la petición. */ }
      messages.replaceChildren();
      quick?.classList.remove('hidden');
      greeted = false;
      greet();
      status.textContent = 'Conversación nueva';
    });
    form.addEventListener('submit', (event) => { event.preventDefault(); send(input.value); });
    quick?.querySelectorAll('button').forEach((button) => button.addEventListener('click', () => send(button.dataset.agentMsg)));
    if (initiallyOpen) greet();
  }

  initAgent({
    toggle: document.getElementById('agentWidgetToggle'),
    panel: document.getElementById('agentWidgetPanel'),
    close: document.getElementById('agentWidgetClose'),
    reset: null,
    messages: document.getElementById('agentMessagesWidget'),
    quick: null,
    form: document.getElementById('agentFormWidget'),
    input: document.getElementById('agentInputWidget'),
    status: document.getElementById('agentStatusWidget'),
    initiallyOpen: false
  });

  initAgent({
    toggle: null,
    panel: document.getElementById('agentWindow'),
    close: document.getElementById('agentClose'),
    reset: document.getElementById('agentReset'),
    messages: document.getElementById('agentMessages'),
    quick: document.getElementById('agentQuick'),
    form: document.getElementById('agentForm'),
    input: document.getElementById('agentInput'),
    status: document.getElementById('agentStatus'),
    initiallyOpen: Boolean(document.getElementById('agentForm'))
  });
})();
