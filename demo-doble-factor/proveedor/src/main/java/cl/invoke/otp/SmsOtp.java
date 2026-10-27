package cl.invoke.otp;

import java.util.Map;
import org.keycloak.authentication.AuthenticationFlowContext;
import org.keycloak.models.UserModel;

/**
 * Código por SMS. En la demo el SMS llega al teléfono simulado (/mi-app/telefono); en producción este
 * método llamaría al proveedor de SMS (Twilio, AWS SNS, operador local) con la misma lógica.
 */
public class SmsOtp extends OtpBase {

    static final String ATRIBUTO = "telefono";

    @Override
    protected String canal() {
        return "sms";
    }

    @Override
    protected String destino(UserModel usuario) {
        return usuario.getFirstAttribute(ATRIBUTO);
    }

    @Override
    protected void enviar(AuthenticationFlowContext ctx, String codigo, Map<String, String> cfg) {
        int min = Math.max(1, entero(cfg, "ttl", 300) / 60);
        String texto = CorreoOtp.nombre(ctx.getRealm()) + ": tu código es " + codigo + ". Vence en " + min
                + " min. No lo compartas.";
        Telefono.publicar(cfg, canal(), destino(ctx.getUser()), null, texto, "SMS simulado");
    }
}
