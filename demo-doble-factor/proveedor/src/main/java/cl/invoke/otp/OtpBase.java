package cl.invoke.otp;

import jakarta.ws.rs.core.MultivaluedMap;
import jakarta.ws.rs.core.Response;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Map;
import org.jboss.logging.Logger;
import org.keycloak.authentication.AuthenticationFlowContext;
import org.keycloak.authentication.AuthenticationFlowError;
import org.keycloak.authentication.Authenticator;
import org.keycloak.common.util.SecretGenerator;
import org.keycloak.common.util.Time;
import org.keycloak.forms.login.LoginFormsProvider;
import org.keycloak.models.AuthenticatorConfigModel;
import org.keycloak.models.KeycloakSession;
import org.keycloak.models.RealmModel;
import org.keycloak.models.UserModel;
import org.keycloak.sessions.AuthenticationSessionModel;

/**
 * Segundo factor con código de un solo uso. Genera el código, lo envía por el canal (correo o SMS),
 * muestra el formulario y lo valida: vence según "ttl" y permite 3 intentos antes de enviar uno nuevo.
 */
public abstract class OtpBase implements Authenticator {

    static final Logger LOG = Logger.getLogger(OtpBase.class);
    static final String N_CODIGO = "invoke-otp-codigo";
    static final String N_VENCE = "invoke-otp-vence";
    static final String N_INTENTOS = "invoke-otp-intentos";
    static final int MAX_INTENTOS = 3;

    /** "correo" o "sms". */
    protected abstract String canal();

    /** Correo o teléfono del usuario; null si no tiene. */
    protected abstract String destino(UserModel usuario);

    /** Envía el código. */
    protected abstract void enviar(AuthenticationFlowContext ctx, String codigo, Map<String, String> cfg);

    static Map<String, String> cfg(AuthenticationFlowContext ctx) {
        AuthenticatorConfigModel c = ctx.getAuthenticatorConfig();
        return c != null && c.getConfig() != null ? c.getConfig() : Map.of();
    }

    static int entero(Map<String, String> cfg, String clave, int porDefecto) {
        try {
            return Integer.parseInt(cfg.getOrDefault(clave, "").trim());
        } catch (NumberFormatException e) {
            return porDefecto;
        }
    }

    @Override
    public void authenticate(AuthenticationFlowContext ctx) {
        nuevoCodigo(ctx);
        ctx.challenge(formulario(ctx, null));
    }

    private void nuevoCodigo(AuthenticationFlowContext ctx) {
        Map<String, String> cfg = cfg(ctx);
        int largo = entero(cfg, "largo", 6);
        int ttl = entero(cfg, "ttl", 300);
        String codigo = SecretGenerator.getInstance().randomString(largo, SecretGenerator.DIGITS);
        AuthenticationSessionModel s = ctx.getAuthenticationSession();
        s.setAuthNote(N_CODIGO, codigo);
        s.setAuthNote(N_VENCE, String.valueOf(Time.currentTime() + ttl));
        s.setAuthNote(N_INTENTOS, "0");
        enviar(ctx, codigo, cfg);
        LOG.infof("Código %s enviado a %s (usuario %s)", canal(), enmascarar(destino(ctx.getUser())), ctx.getUser().getUsername());
    }

    private Response formulario(AuthenticationFlowContext ctx, String error) {
        Map<String, String> cfg = cfg(ctx);
        LoginFormsProvider f = ctx.form()
                .setAttribute("canal", canal())
                .setAttribute("destino", enmascarar(destino(ctx.getUser())))
                .setAttribute("largo", entero(cfg, "largo", 6))
                .setAttribute("ttlMin", Math.max(1, entero(cfg, "ttl", 300) / 60))
                .setAttribute("urlTelefono", cfg.getOrDefault("url-telefono-publica", "/mi-app/telefono"));
        if (error != null) {
            f.setError(error);
        }
        return f.createForm("invoke-otp.ftl");
    }

    @Override
    public void action(AuthenticationFlowContext ctx) {
        MultivaluedMap<String, String> p = ctx.getHttpRequest().getDecodedFormParameters();
        AuthenticationSessionModel s = ctx.getAuthenticationSession();
        if (p.containsKey("reenviar") || s.getAuthNote(N_CODIGO) == null) {
            authenticate(ctx);
            return;
        }
        String ingresado = p.getFirst("codigo") == null ? "" : p.getFirst("codigo").replaceAll("\\s", "");
        String codigo = s.getAuthNote(N_CODIGO);
        long vence = Long.parseLong(s.getAuthNote(N_VENCE));

        if (Time.currentTime() > vence) {
            ctx.failureChallenge(AuthenticationFlowError.EXPIRED_CODE,
                    formulario(ctx, "El código venció. Usa «Enviar otro código»."));
            return;
        }
        if (MessageDigest.isEqual(codigo.getBytes(StandardCharsets.UTF_8), ingresado.getBytes(StandardCharsets.UTF_8))) {
            s.removeAuthNote(N_CODIGO);
            s.removeAuthNote(N_VENCE);
            s.removeAuthNote(N_INTENTOS);
            ctx.success();
            return;
        }
        int intentos = Integer.parseInt(s.getAuthNote(N_INTENTOS)) + 1;
        if (intentos >= MAX_INTENTOS) {
            nuevoCodigo(ctx);
            ctx.failureChallenge(AuthenticationFlowError.INVALID_CREDENTIALS,
                    formulario(ctx, "Código incorrecto " + MAX_INTENTOS + " veces: te enviamos un código nuevo."));
            return;
        }
        s.setAuthNote(N_INTENTOS, String.valueOf(intentos));
        int quedan = MAX_INTENTOS - intentos;
        ctx.failureChallenge(AuthenticationFlowError.INVALID_CREDENTIALS,
                formulario(ctx, "Código incorrecto. Te queda" + (quedan == 1 ? " 1 intento." : "n " + quedan + " intentos.")));
    }

    static String enmascarar(String v) {
        if (v == null) {
            return "";
        }
        int arroba = v.indexOf('@');
        if (arroba > 0) {
            return v.charAt(0) + "•••" + v.substring(arroba);
        }
        String d = v.replaceAll("[^0-9+]", "");
        return d.length() > 4 ? "•••• " + d.substring(d.length() - 4) : v;
    }

    @Override
    public boolean requiresUser() {
        return true;
    }

    @Override
    public boolean configuredFor(KeycloakSession session, RealmModel realm, UserModel user) {
        String d = destino(user);
        return d != null && !d.isBlank();
    }

    @Override
    public void setRequiredActions(KeycloakSession session, RealmModel realm, UserModel user) {
    }

    @Override
    public void close() {
    }
}
