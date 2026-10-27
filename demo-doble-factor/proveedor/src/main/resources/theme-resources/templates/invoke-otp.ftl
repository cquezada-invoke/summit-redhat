<#import "template.ftl" as layout>
<@layout.registrationLayout displayMessage=true; section>
  <#if section = "header">
    <#if canal == "sms">Código por SMS<#else>Código por correo</#if>
  <#elseif section = "form">
    <p style="margin-bottom:16px">
      Te enviamos un código de ${largo} dígitos <#if canal == "sms">por SMS al teléfono<#else>al correo</#if>
      <strong>${destino}</strong>. Vence en ${ttlMin} min.
    </p>
    <form id="kc-otp-login-form" class="${properties.kcFormClass!}" action="${url.loginAction}" method="post">
      <div class="${properties.kcFormGroupClass!}">
        <label for="codigo" class="${properties.kcLabelClass!}">Código</label>
        <input id="codigo" name="codigo" type="text" inputmode="numeric" autocomplete="one-time-code"
               autofocus class="${properties.kcInputClass!}" style="font-size:22px;letter-spacing:6px"/>
      </div>
      <div class="${properties.kcFormGroupClass!}">
        <input class="${properties.kcButtonClass!} ${properties.kcButtonPrimaryClass!} ${properties.kcButtonBlockClass!} ${properties.kcButtonLargeClass!}"
               type="submit" value="Verificar"/>
      </div>
    </form>
    <form action="${url.loginAction}" method="post" style="margin-top:8px">
      <input type="hidden" name="reenviar" value="1"/>
      <button type="submit" class="${properties.kcButtonClass!} ${properties.kcButtonDefaultClass!} ${properties.kcButtonBlockClass!}">Enviar otro código</button>
    </form>
    <p style="margin-top:16px;font-size:13px">
      Demo: <a href="${urlTelefono}" target="_blank" rel="noopener">abrir el teléfono y la bandeja simulados</a>
    </p>
  </#if>
</@layout.registrationLayout>
