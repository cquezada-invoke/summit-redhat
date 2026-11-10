<%@ page import="weblogic.servlet.security.ServletAuthentication" %>
<%@ page import="weblogic.jndi.WLInitialContextFactory" %>
<% int r = ServletAuthentication.weak("operador", request.getParameter("clave"), request); %>
<html><body>Legado: <%= r == ServletAuthentication.AUTHENTICATED ? "autenticado" : "sin autenticar" %></body></html>
