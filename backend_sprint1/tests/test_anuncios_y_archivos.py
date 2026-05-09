"""Tests de anuncios (CRUD + pin + comentarios) y archivos (subir/borrar/categoría)."""

import io


def _crear_clase(client, headers):
    r = client.post("/api/clases", headers=headers, json={
        "nombre": "Mate", "materia": "Matemáticas", "descripcion": "", "es_privada": False,
    })
    assert r.status_code == 201, r.text
    return r.json()


def test_anuncio_crud_y_pin(client, auth):
    clase = _crear_clase(client, auth)

    a1 = client.post(f"/api/clases/{clase['id']}/anuncios", headers=auth, json={
        "titulo": "Examen el viernes", "contenido": "Estudien capítulos 1-3",
    }).json()
    a2 = client.post(f"/api/clases/{clase['id']}/anuncios", headers=auth, json={
        "titulo": "Bienvenida", "contenido": "Hola a todos",
    }).json()

    # Anclar el segundo y verificar que va primero.
    r = client.patch(f"/api/anuncios/{a2['id']}", headers=auth, json={"anclado": True})
    assert r.status_code == 200
    listados = client.get(f"/api/clases/{clase['id']}/anuncios", headers=auth).json()
    assert listados[0]["id"] == a2["id"]
    assert listados[0]["anclado"] is True

    # Editar
    r = client.patch(f"/api/anuncios/{a1['id']}", headers=auth, json={
        "titulo": "Examen el viernes (actualizado)",
    })
    assert r.status_code == 200
    assert r.json()["titulo"] == "Examen el viernes (actualizado)"

    # Eliminar
    assert client.delete(f"/api/anuncios/{a1['id']}", headers=auth).status_code == 204


def test_anuncio_estudiante_no_puede_editar(client, tutor, estudiante):
    auth_t = {"Authorization": f"Bearer {tutor['token']}"}
    auth_e = {"Authorization": f"Bearer {estudiante['token']}"}
    clase = _crear_clase(client, auth_t)
    client.post("/api/inscripciones", headers=auth_e, json={"codigo_clase": clase["codigo_clase"]})
    a = client.post(f"/api/clases/{clase['id']}/anuncios", headers=auth_t, json={
        "titulo": "Anuncio", "contenido": "x",
    }).json()

    # Estudiante puede ver pero no editar/eliminar.
    assert client.get(f"/api/clases/{clase['id']}/anuncios", headers=auth_e).status_code == 200
    assert client.patch(f"/api/anuncios/{a['id']}", headers=auth_e, json={"titulo": "hack"}).status_code == 403
    assert client.delete(f"/api/anuncios/{a['id']}", headers=auth_e).status_code == 403


def test_comentarios(client, tutor, estudiante):
    auth_t = {"Authorization": f"Bearer {tutor['token']}"}
    auth_e = {"Authorization": f"Bearer {estudiante['token']}"}
    clase = _crear_clase(client, auth_t)
    client.post("/api/inscripciones", headers=auth_e, json={"codigo_clase": clase["codigo_clase"]})
    r = client.post(f"/api/clases/{clase['id']}/anuncios", headers=auth_t, json={
        "titulo": "Aviso", "contenido": "Contenido del aviso",
    })
    assert r.status_code == 201, r.text
    a = r.json()

    # Estudiante comenta y tutor también.
    c_est = client.post(f"/api/anuncios/{a['id']}/comentarios", headers=auth_e, json={
        "contenido": "¿Qué incluye?",
    }).json()
    client.post(f"/api/anuncios/{a['id']}/comentarios", headers=auth_t, json={
        "contenido": "Capítulos 1-3",
    })
    assert len(client.get(f"/api/anuncios/{a['id']}/comentarios", headers=auth_e).json()) == 2

    # El tutor puede borrar comentarios ajenos (es dueño de la clase).
    assert client.delete(f"/api/comentarios/{c_est['id']}", headers=auth_t).status_code == 204
    assert len(client.get(f"/api/anuncios/{a['id']}/comentarios", headers=auth_e).json()) == 1


def test_archivos_subir_filtrar_borrar(client, auth):
    clase = _crear_clase(client, auth)

    contenido = b"%PDF-1.4 contenido falso"
    files = {"archivo": ("a.pdf", io.BytesIO(contenido), "application/pdf")}
    r = client.post(
        f"/api/clases/{clase['id']}/archivos", headers=auth, files=files,
        params={"categoria": "Tareas"},
    )
    assert r.status_code == 201
    arch1 = r.json()
    assert arch1["categoria"] == "Tareas"

    files = {"archivo": ("b.pdf", io.BytesIO(contenido), "application/pdf")}
    client.post(
        f"/api/clases/{clase['id']}/archivos", headers=auth, files=files,
        params={"categoria": "Material"},
    )

    # Filtrar por categoría
    r = client.get(f"/api/clases/{clase['id']}/archivos", headers=auth, params={"categoria": "Tareas"})
    assert len(r.json()) == 1

    # Lista de categorías
    r = client.get(f"/api/clases/{clase['id']}/archivos/categorias", headers=auth)
    assert sorted(r.json()) == ["Material", "Tareas"]

    # Borrar
    assert client.delete(f"/api/archivos/{arch1['id']}", headers=auth).status_code == 204
    assert len(client.get(f"/api/clases/{clase['id']}/archivos", headers=auth).json()) == 1


def test_archivo_mime_no_permitido(client, auth):
    clase = _crear_clase(client, auth)
    files = {"archivo": ("malo.exe", io.BytesIO(b"MZ"), "application/x-msdownload")}
    r = client.post(f"/api/clases/{clase['id']}/archivos", headers=auth, files=files)
    assert r.status_code == 400
