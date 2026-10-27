(function () {
  const form = document.getElementById("adminAgentForm");
  const input = document.getElementById("adminAgentInput");
  const messages = document.getElementById("adminAgentMessages");
  const status = document.getElementById("adminAgentStatus");
  const draftCard = document.getElementById("adminProductDraft");
  const draftForm = document.getElementById("adminProductDraftForm");
  if (!form || !input || !messages || !status || !draftCard || !draftForm) return;

  const csrf = document.querySelector('meta[name="csrf-token"]')?.content || "";
  const money = value => "$" + Number(value || 0).toLocaleString("es-CO") + " COP";
  function addMessage(text, role) {
    const node = document.createElement("div");
    node.className = "admin-agent-message " + role;
    node.textContent = String(text || "");
    messages.appendChild(node);
    messages.scrollTop = messages.scrollHeight;
  }
  function showResult(data) {
    if (data.product_draft) {
      const draft = data.product_draft;
      draftForm.elements.name.value = draft.name || "";
      draftForm.elements.description.value = draft.description || "";
      draftForm.elements.price.value = draft.price ?? "";
      draftForm.elements.stock.value = draft.stock ?? "";
      draftForm.elements.category_id.value = draft.category_id ? String(draft.category_id) : "";
      draftForm.elements.featured.checked = Boolean(draft.featured);
      draftCard.hidden = false;
    }
    if (data.overview) {
      const o = data.overview;
      addMessage(
        "Pedidos: " + o.orders_total + ". Por preparar: " + o.orders_to_prepare +
        ". Pagos pendientes: " + o.payments_pending + ". Productos activos: " + o.products_active +
        ". Stock bajo: " + o.products_low_stock + ". Tienda: " + (o.store_status === "open" ? "abierta" : "cerrada") + ".",
        "assistant"
      );
    }
    if (Array.isArray(data.products)) {
      if (!data.products.length) addMessage("No encontré productos que coincidan con esa búsqueda.", "assistant");
      data.products.forEach(product => {
        const state = product.active ? "Activo" : "Inactivo";
        addMessage(
          product.name + " · " + money(product.price) + " · stock " + product.stock + " · " + state +
          (product.category ? " · " + product.category : ""),
          "assistant"
        );
      });
    }
  }
  async function sendMessage(message) {
    const text = String(message || "").trim();
    if (!text) return;
    addMessage(text, "user");
    status.textContent = "Consultando el catálogo y los datos de la tienda…";
    form.querySelector("button").disabled = true;
    try {
      const response = await fetch("/api/admin/agent", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
        body: JSON.stringify({ message: text })
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "No pude completar la solicitud.");
      if (data.reply) addMessage(data.reply, "assistant");
      showResult(data);
      status.textContent = data.mode === "product-draft" ? "Propuesta pendiente de revisión" : "Consulta completada";
    } catch (error) {
      addMessage(error.message || "Error de conexión.", "error");
      status.textContent = "No se realizó ningún cambio";
    } finally {
      form.querySelector("button").disabled = false;
      input.focus();
    }
  }

  form.addEventListener("submit", event => {
    event.preventDefault();
    const message = input.value;
    input.value = "";
    sendMessage(message);
  });
  document.querySelectorAll("[data-admin-agent-prompt]").forEach(button => {
    button.addEventListener("click", () => sendMessage(button.dataset.adminAgentPrompt));
  });
  draftForm.addEventListener("submit", async event => {
    event.preventDefault();
    const button = draftForm.querySelector('button[type="submit"]');
    const payload = {
      name: draftForm.elements.name.value.trim(),
      description: draftForm.elements.description.value.trim(),
      price: Number(draftForm.elements.price.value),
      stock: Number(draftForm.elements.stock.value),
      category_id: Number(draftForm.elements.category_id.value),
      featured: draftForm.elements.featured.checked,
      confirmed: true
    };
    button.disabled = true;
    button.textContent = "Añadiendo producto…";
    try {
      const response = await fetch("/api/admin/agent/products", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
        body: JSON.stringify(payload)
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "No se pudo crear el producto.");
      addMessage(data.message + " Ya puedes revisarlo en Productos.", "assistant");
      draftCard.hidden = true;
      draftForm.reset();
      status.textContent = "Producto añadido y registrado en auditoría";
    } catch (error) {
      addMessage(error.message || "Error al crear el producto.", "error");
      status.textContent = "El producto no se añadió";
    } finally {
      button.disabled = false;
      button.textContent = "Confirmar y añadir al catálogo";
    }
  });
})();
