package cl.invoke.otp;

import java.util.Map;
import org.keycloak.authentication.AuthenticationFlowContext;
import org.keycloak.email.EmailSenderProvider;
import org.keycloak.models.RealmModel;
import org.keycloak.models.UserModel;

/**
 * Código por correo. Usa el SMTP del realm (Realm settings → Email). Si el SMTP no está listo
 * (por ejemplo, falta la contraseña de aplicación de Gmail), el código igual llega a la bandeja simulada.
 */
public class CorreoOtp extends OtpBase {

    @Override
    protected String canal() {
        return "correo";
    }

    @Override
    protected String destino(UserModel usuario) {
        return usuario.getEmail();
    }

    @Override
    protected void enviar(AuthenticationFlowContext ctx, String codigo, Map<String, String> cfg) {
        UserModel u = ctx.getUser();
        RealmModel r = ctx.getRealm();
        int min = Math.max(1, entero(cfg, "ttl", 300) / 60);
        String asunto = "Tu código de acceso: " + codigo;
        String texto = "Hola " + (u.getFirstName() == null ? u.getUsername() : u.getFirstName()) + ",\n\n"
                + "Tu código para ingresar a " + nombre(r) + " es " + codigo + ".\n"
                + "Vence en " + min + " minutos. Si no fuiste tú, ignora este correo.";
        String html = "<div style=\"font-family:Arial,sans-serif;max-width:480px\">"
                + "<p>Hola " + esc(u.getFirstName() == null ? u.getUsername() : u.getFirstName()) + ",</p>"
                + "<p>Tu código para ingresar a <b>" + esc(nombre(r)) + "</b> es:</p>"
                + "<p style=\"font-size:32px;letter-spacing:6px;font-weight:bold;margin:16px 0\">" + codigo + "</p>"
                + "<p style=\"color:#667085\">Vence en " + min + " minutos. Si no fuiste tú, ignora este correo.</p></div>";

        String nota;
        Map<String, String> smtp = r.getSmtpConfig();
        if (smtp == null || smtp.get("host") == null || smtp.get("host").isBlank()) {
            nota = "Sin SMTP configurado: solo bandeja simulada";
        } else {
            try {
                ctx.getSession().getProvider(EmailSenderProvider.class).send(smtp, u, asunto, texto, html);
                nota = "Enviado también a " + u.getEmail();
            } catch (Exception e) {
                LOG.warnf("SMTP no disponible (%s): %s", smtp.get("host"), e.getMessage());
                nota = "SMTP no disponible: solo bandeja simulada";
            }
        }
        Telefono.publicar(cfg, canal(), u.getEmail(), asunto, texto, nota);
    }

    static String nombre(RealmModel r) {
        return r.getDisplayName() != null && !r.getDisplayName().isBlank() ? r.getDisplayName() : r.getName();
    }

    static String esc(String s) {
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;");
    }
}
