# AGENTS.md — Endura Human

## Misión
Mantener y desarrollar la plataforma de gestión de Endura Human (suplementación para deportes de resistencia): inventario por lotes y vencimientos, ventas y cartera, patrocinios, compras, gastos, socios y resultados. Arquitectura hermana de Gatelier (`jgiral95/gatelier`).

## Reglas
- Responder y documentar en español.
- Usar el logo original (`frontend/assets/endura-logo*.png`, `endura-icon.png`); no redibujarlo ni cambiar sus proporciones. Paleta: negro `#0b0b0f`, violeta `#7a29e8`, naranja `#f3893d`, blanco hueso `#eae6e1`.
- No modificar `.env` ni `compose.yaml` del servidor sin autorización; no publicar la API ni PostgreSQL en puertos del host.
- No borrar ni reinicializar volúmenes o tablas. Migraciones solo aditivas e idempotentes (`SCHEMA` en `main.py`).
- Nunca versionar secretos, el Excel con datos reales ni dumps.
- No afirmar que pruebas, despliegues o migraciones funcionaron sin ejecutarlos.

## Invariantes del dominio
- `products.quantity` = suma de `lots.quantity`; siempre se recalcula con `refresh_product`.
- Salidas por FEFO (`take_stock`), registrando lote y costo en `movements` y `sale_item_lots`.
- Ventas/patrocinios conservan su costo histórico; anular devuelve unidades a sus lotes y conserva el registro.
- Compras: costo aterrizado = precio × tasa + (envío + otros) repartido por valor de línea.
- Vencimientos de mes/año se guardan como último día del mes.
- Registros `imported=TRUE` vienen del Excel: no movieron stock y su costo es estimado.
- Acciones destructivas solo para `admin`; nada se borra físicamente salvo pagos mal registrados.

## Proceso
1. Leer el código real antes de cambiarlo. 2. Cambios localizados. 3. Pruebas en `backend/tests` para cada regla nueva. 4. Revisar las páginas en navegador (escritorio y móvil). 5. Documentar en README.
