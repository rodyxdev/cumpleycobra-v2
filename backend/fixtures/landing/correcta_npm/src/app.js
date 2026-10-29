document.getElementById("form-contacto").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const datos = {
    nombre: form.nombre.value.trim(),
    email: form.email.value.trim(),
    mensaje: form.mensaje.value.trim(),
  };
  const respuesta = await fetch("/api/contacto", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(datos),
  });
  if (respuesta.ok) {
    form.reset();
    document.getElementById("confirmacion").hidden = false;
  }
});
