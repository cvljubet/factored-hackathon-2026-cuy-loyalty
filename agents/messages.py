"""Fixed, reviewed replies used whenever the answer must not come from a model."""

from agents.context import Language

_MESSAGES: dict[str, dict[Language, str]] = {
    "sensitive_request": {
        "es": (
            "Por tu seguridad, no puedo consultar ni comentar tu puntaje crediticio ni tus ingresos por este canal. "
            "Puedes revisarlos en la app del banco o con un asesor en una sucursal."
        ),
        "pt": (
            "Para sua segurança, não posso consultar nem comentar seu score de crédito ou sua renda por este canal. "
            "Você pode verificá-los no app do banco ou com um atendente em uma agência."
        ),
    },
    "out_of_scope": {
        "es": (
            "Puedo ayudarte con tu perfil, tus productos, beneficios, campañas, gastos y sucursales. "
            "¿Sobre cuál de estos temas quieres consultar?"
        ),
        "pt": (
            "Posso ajudar com seu perfil, seus produtos, benefícios, campanhas, gastos e agências. "
            "Sobre qual desses temas você quer saber?"
        ),
    },
    "inquiry_failed": {
        "es": "Lo siento, no pude responder tu consulta en este momento. ¿Puedes intentarlo de nuevo?",
        "pt": "Desculpe, não consegui responder sua pergunta agora. Pode tentar novamente?",
    },
    "output_blocked": {
        "es": (
            "Lo siento, no puedo mostrar esa respuesta porque podría contener datos sensibles. "
            "¿Puedes reformular tu consulta?"
        ),
        "pt": (
            "Desculpe, não posso mostrar essa resposta porque ela pode conter dados sensíveis. "
            "Pode reformular sua pergunta?"
        ),
    },
    "input_blocked": {
        "es": "No puedo ayudarte con ese mensaje. ¿Hay algo más sobre tus productos o beneficios en que pueda ayudarte?",
        "pt": "Não posso ajudar com essa mensagem. Posso ajudar com algo sobre seus produtos ou benefícios?",
    },
    # The loyalty recommendation, worded without a model (local runs, and whenever the model's wording is refused).
    "benefit_recommendation": {
        "es": "Te recomiendo este beneficio: {offer_title}. {offer_description} {customer_safe_reason} ({illustrative_note})",
        "pt": "Recomendo este benefício: {offer_title}. {offer_description} {customer_safe_reason} ({illustrative_note})",
    },
    "no_product_advice": {
        "es": "No puedo recomendarte productos financieros específicos, pero sí un beneficio de tu programa de fidelidad.",
        "pt": "Não posso recomendar produtos financeiros específicos, mas posso indicar um benefício do seu programa de "
              "fidelidade.",
    },
    # Same for every escalation reason. It does not claim a human is already replying.
    "handoff_acknowledgement": {
        "es": (
            "He derivado tu solicitud a un asesor. El contexto de esta conversación será incluido "
            "para que no tengas que repetir la información."
        ),
        "pt": (
            "Encaminhei sua solicitação para um atendente. O contexto desta conversa será incluído "
            "para que você não precise repetir as informações."
        ),
    },
}


def message(key: str, language: Language, **values: str) -> str:
    return _MESSAGES[key][language].format(**values)
