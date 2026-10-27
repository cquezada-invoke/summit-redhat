package cl.invoke.otp;

import java.util.List;
import org.keycloak.Config;
import org.keycloak.authentication.Authenticator;
import org.keycloak.authentication.AuthenticatorFactory;
import org.keycloak.models.AuthenticationExecutionModel.Requirement;
import org.keycloak.models.KeycloakSession;
import org.keycloak.models.KeycloakSessionFactory;
import org.keycloak.provider.ProviderConfigProperty;
import org.keycloak.provider.ProviderConfigurationBuilder;

/** Fábricas que registran los dos autenticadores en Keycloak. */
public final class Fabricas {

    private Fabricas() {
    }

    static final Requirement[] OPCIONES = {Requirement.REQUIRED, Requirement.ALTERNATIVE, Requirement.DISABLED};

    static final List<ProviderConfigProperty> PROPIEDADES = ProviderConfigurationBuilder.create()
            .property().name("largo").label("Dígitos del código").type(ProviderConfigProperty.STRING_TYPE)
            .defaultValue("6").helpText("Cantidad de dígitos.").add()
            .property().name("ttl").label("Vigencia (segundos)").type(ProviderConfigProperty.STRING_TYPE)
            .defaultValue("300").helpText("Tiempo que el código es válido.").add()
            .property().name("url-telefono").label("URL interna del teléfono simulado")
            .type(ProviderConfigProperty.STRING_TYPE).defaultValue("http://192.168.122.1:8096/interno/mensaje")
            .helpText("Donde se publica la copia del mensaje para la demo.").add()
            .property().name("url-telefono-publica").label("URL pública del teléfono simulado")
            .type(ProviderConfigProperty.STRING_TYPE).defaultValue("/mi-app/telefono")
            .helpText("Enlace que se muestra en el formulario del código.").add()
            .build();

    abstract static class Base implements AuthenticatorFactory {
        @Override
        public boolean isConfigurable() {
            return true;
        }

        @Override
        public Requirement[] getRequirementChoices() {
            return OPCIONES;
        }

        @Override
        public boolean isUserSetupAllowed() {
            return false;
        }

        @Override
        public List<ProviderConfigProperty> getConfigProperties() {
            return PROPIEDADES;
        }

        @Override
        public void init(Config.Scope config) {
        }

        @Override
        public void postInit(KeycloakSessionFactory factory) {
        }

        @Override
        public void close() {
        }
    }

    public static class Correo extends Base {
        private static final CorreoOtp INSTANCIA = new CorreoOtp();

        @Override
        public String getId() {
            return "invoke-otp-correo";
        }

        @Override
        public String getDisplayType() {
            return "Código por correo";
        }

        @Override
        public String getReferenceCategory() {
            return "otp-correo";
        }

        @Override
        public String getHelpText() {
            return "Te enviamos un código a tu correo.";
        }

        @Override
        public Authenticator create(KeycloakSession session) {
            return INSTANCIA;
        }
    }

    public static class Sms extends Base {
        private static final SmsOtp INSTANCIA = new SmsOtp();

        @Override
        public String getId() {
            return "invoke-otp-sms";
        }

        @Override
        public String getDisplayType() {
            return "Código por SMS";
        }

        @Override
        public String getReferenceCategory() {
            return "otp-sms";
        }

        @Override
        public String getHelpText() {
            return "Te enviamos un código por SMS a tu teléfono.";
        }

        @Override
        public Authenticator create(KeycloakSession session) {
            return INSTANCIA;
        }
    }
}
