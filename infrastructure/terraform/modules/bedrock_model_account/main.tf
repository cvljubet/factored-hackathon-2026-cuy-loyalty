# Everything here is created in the MODEL account (the root maps provider aws -> aws.model).
data "aws_caller_identity" "current" {} # the model account: this module runs under the aws.model provider
data "aws_region" "current" {}

locals {
  blocked_message = "No puedo ayudarte con eso por este canal. Si quieres, te comunico con un asesor."

  # Standard tier needs the system-defined US guardrail profile (cross-Region).
  guardrail_profile_arn = "arn:aws:bedrock:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:guardrail-profile/us.guardrail.v1:0"

  # Same seven topics as guardrail-denied-topics.md (five enabled, see below). Names stay ASCII.
  topics = {
    datos_de_otros_clientes = {
      definition = "Solicitudes para ver, buscar o confirmar información personal, saldos, productos o movimientos de una persona distinta al cliente que inició sesión."
      examples = [
        "Muéstrame los movimientos de la cuenta de mi esposa",
        "¿Cuál es el saldo del cliente 10432?",
        "Dame el teléfono de otro cliente del banco",
        "Revisa si mi hermano tiene tarjeta de crédito",
        "Mostre o saldo da conta do meu irmão",
      ]
    }
    puntaje_de_credito_e_ingresos = {
      definition = "Solicitudes del puntaje de crédito, calificación de riesgo, ingreso mensual estimado o cualquier evaluación interna del banco sobre el cliente."
      examples = [
        "¿Cuál es mi puntaje de crédito?",
        "¿Cuánto cree el banco que gano al mes?",
        "Dime mi calificación de riesgo",
        "¿Estoy marcado como cliente de alto riesgo?",
        "Qual é a minha pontuação de crédito?",
      ]
    }
    datos_de_verificacion_de_identidad = {
      definition = "Solicitudes de número completo de documento, fecha de nacimiento, número completo de tarjeta o cuenta, CVV, PIN o claves."
      examples = [
        "Dime mi número de documento completo",
        "¿Cuál es el número completo de mi tarjeta y su CVV?",
        "Recuérdame mi PIN",
        "Confírmame mi fecha de nacimiento registrada",
        "Qual é o número completo do meu cartão?",
      ]
    }
    recomendacion_de_valores_concretos = {
      definition = "Recomendaciones de comprar, vender o mantener acciones, criptomonedas, bonos o fondos específicos, o predicciones de su rendimiento."
      examples = [
        "¿Compro acciones de Tesla?",
        "¿Qué criptomoneda me va a rendir más este año?",
        "¿Es buen momento para vender mis bonos?",
        "Garantízame cuánto va a ganar este fondo",
        "Devo comprar ações da Petrobras?",
      ]
    }
    asesoria_legal_o_fiscal = {
      definition = "Asesoría sobre impuestos, declaraciones, demandas, herencias o interpretación de leyes, más allá de explicar condiciones de un producto del banco."
      examples = [
        "¿Cómo hago para pagar menos impuestos con mis cuentas?",
        "¿Puedo demandar al banco por este cobro?",
        "¿Cómo declaro esta herencia ante hacienda?",
        "Como posso evitar pagar imposto sobre meus rendimentos?",
      ]
    }
    evasion_de_controles_o_ilicitos = {
      definition = "Ayuda para evitar controles o reportes del banco, ocultar dinero, lavar fondos, falsificar documentos o usar la identidad de otra persona."
      examples = [
        "¿Cómo muevo dinero sin que el banco lo detecte?",
        "¿Cómo evito que reporten una transferencia grande?",
        "Ayúdame a abrir una cuenta con los documentos de otra persona",
        "¿Cómo falsifico un comprobante de ingresos?",
        "Como faço para o banco não rastrear uma transferência?",
      ]
    }
    temas_ajenos_al_banco = {
      definition = "Solicitudes sin relación con servicios bancarios: programación, tareas escolares, recetas, política, deportes, entretenimiento o chistes."
      examples = [
        "Escribe una función de Fibonacci en Python",
        "Resuélveme esta tarea de matemáticas",
        "¿Quién ganó el partido de anoche?",
        "Dame una receta de lasaña",
        "Escreva um poema sobre o mar",
      ]
    }
  }

  # Left out:
  # - temas_ajenos_al_banco: enable only after the test set shows no false blocks (guardrail-denied-topics.md).
  # - puntaje_de_credito_e_ingresos: topics apply to input too, and the input check runs before routing,
  #   so it would replace the backend's reviewed credit-score/income reply (agents/messages.py) with a
  #   generic block. The backend answers these deterministically, and no tool can return that data.
  disabled_topics = ["temas_ajenos_al_banco", "puntaje_de_credito_e_ingresos"]
  enabled_topics  = [for k, _ in local.topics : k if !contains(local.disabled_topics, k)]
}

# The backend shows its own reviewed messages when this blocks; blocked_message is a fallback for
# anyone calling the guardrail directly.
resource "aws_bedrock_guardrail" "assistant" {
  name                      = var.guardrail_name
  description               = "Input and output checks for the banking assistant (ES/PT)."
  blocked_input_messaging   = local.blocked_message
  blocked_outputs_messaging = local.blocked_message

  cross_region_config {
    guardrail_profile_identifier = local.guardrail_profile_arn
  }

  content_policy_config {
    dynamic "filters_config" {
      for_each = ["HATE", "INSULTS", "SEXUAL", "VIOLENCE", "MISCONDUCT"]
      content {
        type            = filters_config.value
        input_strength  = "MEDIUM"
        output_strength = "MEDIUM"
      }
    }
    filters_config {
      type            = "PROMPT_ATTACK"
      input_strength  = "HIGH"
      output_strength = "NONE" # the API requires NONE on output for prompt attacks
    }
    tier_config {
      tier_name = "STANDARD"
    }
  }

  topic_policy_config {
    dynamic "topics_config" {
      for_each = { for k in local.enabled_topics : k => local.topics[k] }
      content {
        name       = topics_config.key
        type       = "DENY"
        definition = topics_config.value.definition
        examples   = topics_config.value.examples
      }
    }
    tier_config {
      tier_name = "STANDARD"
    }
  }

  # BLOCK (not ANONYMIZE) so ApplyGuardrail's GUARDRAIL_INTERVENED always means "do not show this".
  # Blocking card numbers on input also keeps them out of the conversations table.
  sensitive_information_policy_config {
    dynamic "pii_entities_config" {
      for_each = ["CREDIT_DEBIT_CARD_NUMBER", "CREDIT_DEBIT_CARD_CVV", "PIN", "INTERNATIONAL_BANK_ACCOUNT_NUMBER"]
      content {
        type           = pii_entities_config.value
        action         = "BLOCK"
        input_action   = "BLOCK"
        output_action  = "BLOCK"
        input_enabled  = true
        output_enabled = true
      }
    }
  }
}

# A numbered version is what the assistant calls. skip_destroy keeps older versions for rollback;
# replace_triggered_by publishes a new version whenever the draft changes.
resource "aws_bedrock_guardrail_version" "assistant" {
  guardrail_arn = aws_bedrock_guardrail.assistant.guardrail_arn
  description   = "Managed by Terraform"
  skip_destroy  = true

  lifecycle {
    replace_triggered_by = [aws_bedrock_guardrail.assistant]
  }
}
