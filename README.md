# Endura Human · Plataforma de gestión

Panel interno para Endura Human, emprendimiento de suplementación para deportes de resistencia (geles, drink mix, electrolitos, recuperación). Reemplaza el Excel «Endura Human.xlsx» y sigue la misma arquitectura del panel de Gatelier: FastAPI + PostgreSQL + páginas estáticas servidas por Nginx, todo con Docker Compose.

## Módulos

| Página | Qué resuelve (hoja del Excel que reemplaza) |
|---|---|
| **Inventario** `/admin/` | Productos por marca y categoría, existencias **por lote con fecha de vencimiento**, costo promedio ponderado, precio y margen, alertas de stock mínimo, por vencer (90 días) y vencidos. Entradas y salidas manuales con motivo, historial de movimientos. *(Inventario, Criterios)* |
| **Ventas** | Venta con numeración consecutiva, cliente (con autocompletado), descuento en % o valor, domicilio cobrado, quién vendió o recomendó, estado de entrega y pagos parciales. El stock se descuenta por **FEFO** (primero lo que vence antes) y cada línea guarda su costo real. Cartera por cobrar, abonos y anulación (admin) que devuelve las unidades a sus lotes. Factura imprimible. *(BD Ventas, Factura)* |
| **Patrocinios** | Salidas sin venta: embajadores, activaciones y carreras, intercambios, uso interno, producto dañado o vencido. Registra valor comercial y costo real. *(Patrocinios, Embajadores)* |
| **Compras** | Pedidos nacionales o importados, quién financia (socio o recompra), moneda y tasa, envío e impuestos. Al **recibir** se crean los lotes con vencimiento y **costo aterrizado** (precio × tasa + envío y otros repartidos por valor de línea). Edición, cancelación y reversión de recepción si nada se ha movido. *(Compras)* |
| **Gastos** | Domicilios, transportadoras, marketing, redes, empaques, impuestos (4×1000), plataformas. *(Contabilidad)* |
| **Resultados** | Estado de resultados mensual: ventas, descuentos, costo de lo vendido, margen, domicilios, patrocinios, gastos, ajustes → resultado. Cobrado por medio de pago, cartera, compras del mes, inventario a costo/precio, vencimientos, ventas por marca, producto y vendedor, tendencia de 6 meses. |
| **Socios** | Aportes en dinero y especie, retiros y participación; compras por quién financió. *(Contabilidad: tabla de aportantes)* |
| **Ajustes** (admin) | Usuarios y roles, datos del negocio para la factura. |

No se migraron, por ahora: Mercadeo (precios de la competencia), Presupuestos (cotizaciones), Envíos (cotización de empaques), FER (cálculo de hidratación para eventos) e Intermediarios (venta en consignación).

## Reglas de negocio

- La existencia de un producto es siempre la suma de sus lotes; no se permite stock negativo.
- Un vencimiento impreso como mes/año vale hasta el último día de ese mes.
- Ventas y patrocinios guardan el costo de los lotes que consumieron; cambiar costos después no altera el histórico.
- El domicilio cobrado se reporta aparte del ingreso por productos; el domicilio pagado al mensajero va en Gastos.
- Las compras no son gasto: el producto se vuelve costo cuando se vende o se entrega. Los fletes de importación van dentro de la compra, no en Gastos.
- Solo un administrador anula ventas, patrocinios y gastos, elimina pagos, gestiona marcas, socios y usuarios. Nada se borra: queda anulado con motivo.
- Un formulario de venta enviado dos veces no duplica la venta.

## Estructura

```
backend/app/main.py          API (esquema aditivo e idempotente al arrancar)
backend/app/import_excel.py  Importador del Excel histórico
backend/app/create_user.py   Crear cuenta (con --admin para la primera)
backend/app/promote_admin.py Convertir una cuenta en administradora
backend/tests/               Pruebas contra PostgreSQL desechable
frontend/admin/              Panel (HTML + admin.css + app.js compartido)
frontend/assets/             Logo e isotipo, tipografías alojadas (OFL)
compose.yaml, nginx.conf     Servicios y proxy de /api/
```

## Puesta en marcha

1. `cp .env.example .env` y reemplaza contraseña y `SESSION_SECRET` (`openssl rand -hex 32`).
2. `docker compose up -d --build` y abre `http://localhost:8086` (o `APP_PORT`). Para probar en `http://localhost` sin HTTPS pon `COOKIE_SECURE=false`.
3. Primera cuenta: `docker compose exec api python -m app.create_user TU_USUARIO --admin`.
4. Importar el Excel (solo sobre una base vacía):
   ```
   docker compose cp "Endura Human.xlsx" api:/tmp/endura.xlsx
   docker compose exec api python -m app.import_excel /tmp/endura.xlsx --dry-run   # revisar resumen
   docker compose exec api python -m app.import_excel /tmp/endura.xlsx
   ```

El Compose solo publica el frontend; PostgreSQL y la API quedan internos. Antes de conectar a una base existente, respáldala.

### Qué hace el importador

- Productos y marcas desde **Inventario**. La existencia inicial es EXISTENCIAS, repartida en lotes según VENCIMIENTO 1/2/3 (lo vendido se asume salido de lo que vencía antes).
- **BD Ventas** y **Patrocinios** como historial que no vuelve a mover stock; su costo es el costo actual del producto y queda marcado como *estimado*. Las ventas «Culminado» que se cobraron por menos (redondeos) registran la diferencia como descuento; las demás diferencias quedan en cartera (en el Excel actual coinciden con las 5 marcadas «Deuda: Sí»).
- **Compras** como historial recibido (sin detalle por producto).
- **Contabilidad**: gastos operativos (sin compras de producto ni fletes de importación Deblex/DHL/Letsbox, que ya están en Compras) y la tabla de aportes de socios.
- Datos de la hoja **Factura** como datos del negocio.

Con el Excel de octubre de 2026: 242 productos, 3.529 unidades, 115 ventas, 17 patrocinios, 62 compras, 52 gastos y 5 socios.

## Pruebas

```
pip install -r backend/requirements.txt pytest httpx
TEST_DATABASE_URL=postgresql://USUARIO@localhost:5432/endura_test python -m pytest backend/tests
```

La base debe terminar en `_test` (se borra y recrea). Cubren lotes y FEFO, costo ponderado, salidas, vencimientos, ventas con descuento, domicilio y pagos parciales, numeración e idempotencia, stock insuficiente, anulación con devolución a lotes, patrocinios, costo aterrizado de compras, reversión de recepción, resultado mensual, permisos y socios.
