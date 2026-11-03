<%@ page contentType="text/html; charset=UTF-8" import="java.util.List" %><!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Pedidos</title>
<style>body{font-family:sans-serif;margin:32px;color:#222}table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:6px 10px}
th{background:#eee}.srv{color:#666;font-size:13px}input{margin-right:6px}</style></head>
<body>
<h1>Pedidos</h1>
<p class="srv">Servidor: <%= request.getAttribute("servidor") %> · visitas en esta sesión: <%= session.getAttribute("visitas") %></p>
<table><tr><th>#</th><th>Cliente</th><th>Producto</th><th>Cantidad</th><th>Fecha</th></tr>
<% for (String[] p : (List<String[]>) request.getAttribute("pedidos")) { %>
<tr><td><%= p[0] %></td><td><%= p[1].replace("<", "&lt;") %></td><td><%= p[2].replace("<", "&lt;") %></td><td><%= p[3] %></td><td><%= p[4] %></td></tr>
<% } %>
</table>
<h3>Nuevo pedido</h3>
<form method="post" action="<%= request.getContextPath() %>/pedidos">
<input name="cliente" placeholder="Cliente" required><input name="producto" placeholder="Producto" required>
<input name="cantidad" placeholder="Cantidad" pattern="[0-9]+" required><button>Registrar</button></form>
</body></html>
