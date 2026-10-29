import { useState } from "react";

const SERVICIOS = [
  { titulo: "Caja semanal", texto: "Diez variedades de temporada cada lunes." },
  { titulo: "Caja familiar", texto: "Doble cantidad para hogares de cuatro o más." },
  { titulo: "Pedido a medida", texto: "Elige producto por producto desde tu cuenta." },
];

function Contacto() {
  const [datos, setDatos] = useState({ nombre: "", email: "", mensaje: "" });
  const [enviado, setEnviado] = useState(false);

  const cambiar = (campo) => (e) => setDatos({ ...datos, [campo]: e.target.value });

  async function enviar(e) {
    e.preventDefault();
    const respuesta = await fetch("/api/contacto", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(datos),
    });
    if (respuesta.ok) {
      setDatos({ nombre: "", email: "", mensaje: "" });
      setEnviado(true);
    }
  }

  return (
    <section id="contacto" className="wrap contact">
      <h2>Contacto</h2>
      <form onSubmit={enviar} noValidate>
        <label htmlFor="nombre">Nombre</label>
        <input id="nombre" value={datos.nombre} onChange={cambiar("nombre")} />
        <label htmlFor="email">Email</label>
        <input id="email" type="email" value={datos.email} onChange={cambiar("email")} />
        <label htmlFor="mensaje">Mensaje</label>
        <textarea id="mensaje" rows={4} value={datos.mensaje} onChange={cambiar("mensaje")} />
        <button type="submit">Enviar</button>
      </form>
      {enviado && <p className="ok">¡Gracias! Te responderemos pronto.</p>}
    </section>
  );
}

export default function App() {
  return (
    <>
      <header className="topbar">
        <div className="wrap topbar-inner">
          <a className="brand" href="#">Huerta Viva</a>
          <nav aria-label="Principal">
            <a href="#servicios">Servicios</a>
            <a href="#contacto">Contacto</a>
          </nav>
        </div>
      </header>
      <main>
        <section className="hero">
          <div className="wrap">
            <h1>Verduras orgánicas a tu puerta</h1>
            <p>Cajas semanales de productores locales, cosechadas el día anterior.</p>
            <a className="cta" href="#contacto">Pedir ahora</a>
          </div>
        </section>
        <section id="servicios" className="wrap">
          <h2>Servicios</h2>
          <div className="cards">
            {SERVICIOS.map((s) => (
              <article className="card" key={s.titulo}>
                <h3>{s.titulo}</h3>
                <p>{s.texto}</p>
              </article>
            ))}
          </div>
        </section>
        <Contacto />
      </main>
      <footer className="wrap footer">© 2026 Huerta Viva · Santiago</footer>
    </>
  );
}
