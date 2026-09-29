# Configuración del lado de Chatwoot para el bot de reclutamiento.
#
# Se corre con `rails runner` dentro del contenedor web de Chatwoot:
#
#   ssh vps-adlek 'C=$(docker ps -q -f name=chatwoot_chatwoot\.1 | head -1); \
#     docker exec -i -e STEP=revisar $C bundle exec rails runner -' < scripts/chatwoot_setup.rb
#
# Pasos (variable STEP):
#   revisar      Solo muestra el estado actual. No cambia nada.
#   preparar     Crea etiquetas, atributos de conversación y el Agent Bot
#                (BOT_URL=https://.../webhooks/chatwoot). NO lo conecta a la bandeja.
#   conectar     Resuelve las conversaciones abiertas de la bandeja y le asigna el bot.
#   desconectar  Botón de pánico: quita el bot de la bandeja (vuelve a ser solo humana).
#
# Nunca imprime el token de acceso ni el Webhook Secret del bot: se copian
# desde Ajustes → Bots en la interfaz de Chatwoot.

ACCOUNT_ID = 1
INBOX_ID = (ENV['INBOX_ID'] || 3).to_i
BOT_NAME = 'Reclutamiento Demo'.freeze

LABELS = {
  'reclutamiento' => ['#1F93FF', 'Conversación del bot de reclutamiento'],
  'califica' => ['#44CE4B', 'Candidato que cumple el perfil'],
  'revisar' => ['#FFC532', 'Candidato a revisar (faltan documentos o fuera de ruta)'],
  'entrevista-agendada' => ['#7B61FF', 'Agendó entrevista'],
  'confirmó-asistencia' => ['#0AA06E', 'Confirmó asistencia en el recordatorio'],
  'cambio-de-horario' => ['#FF8A00', 'Pidió cambiar el horario de su entrevista'],
  'abandonó' => ['#F14343', 'Dejó de responder a medio flujo']
}.freeze

ATTRIBUTES = {
  'candidato_nombre' => 'Nombre del candidato',
  'candidato_edad' => 'Edad',
  'candidato_municipio' => 'Municipio',
  'candidato_turno' => 'Turno',
  'candidato_documentos' => 'Documentos',
  'candidato_experiencia' => 'Experiencia',
  'clasificacion' => 'Clasificación',
  'entrevista_horario' => 'Horario de entrevista',
  'entrevista_asistencia' => 'Asistencia',
  'etapa' => 'Etapa',
  'vacante' => 'Vacante'
}.freeze

account = Account.find(ACCOUNT_ID)
inbox = account.inboxes.find(INBOX_ID)
step = ENV.fetch('STEP', 'revisar')

def show_status(account, inbox)
  puts "Cuenta: #{account.name} (#{account.id}) · Bandeja: #{inbox.name} (#{inbox.id})"
  existing_labels = account.labels.where(title: LABELS.keys).pluck(:title)
  puts "Etiquetas: #{existing_labels.size}/#{LABELS.size} #{existing_labels.sort.inspect}"
  existing_attrs = account.custom_attribute_definitions.conversation_attribute
                          .where(attribute_key: ATTRIBUTES.keys).pluck(:attribute_key)
  puts "Atributos de conversación: #{existing_attrs.size}/#{ATTRIBUTES.size}"
  bot = account.agent_bots.find_by(name: BOT_NAME)
  if bot
    puts "Agent Bot: id=#{bot.id} · url=#{bot.outgoing_url.to_s.split('?').first}"
  else
    puts 'Agent Bot: (no existe)'
  end
  assigned = inbox.agent_bot_inbox
  puts "Bot asignado a la bandeja: #{assigned ? "#{assigned.agent_bot&.name} (#{assigned.status})" : 'ninguno'}"
  counts = Conversation.where(inbox_id: inbox.id).group(:status).count
  puts "Conversaciones en la bandeja por estado: #{counts.inspect}"
end

case step
when 'revisar'
  show_status(account, inbox)

when 'preparar'
  url = ENV['BOT_URL'].to_s.strip
  abort 'Falta BOT_URL=https://.../webhooks/chatwoot' unless url.start_with?('https://')

  LABELS.each do |title, (color, description)|
    label = account.labels.find_or_initialize_by(title: title)
    created = label.new_record?
    if created
      label.color = color
      label.description = description
      label.show_on_sidebar = true if label.respond_to?(:show_on_sidebar=)
      label.save!
    end
    puts "Etiqueta #{created ? 'creada' : 'ya existía'}: #{title}"
  end

  ATTRIBUTES.each do |key, name|
    definition = account.custom_attribute_definitions.find_or_initialize_by(
      attribute_key: key, attribute_model: :conversation_attribute
    )
    created = definition.new_record?
    if created
      definition.attribute_display_name = name
      definition.attribute_display_type = :text
      definition.attribute_description = 'Lo llena el bot de reclutamiento'
      definition.save!
    end
    puts "Atributo #{created ? 'creado' : 'ya existía'}: #{key}"
  end

  bot = account.agent_bots.find_or_initialize_by(name: BOT_NAME)
  created = bot.new_record?
  bot.description = 'Bot de reclutamiento (demo Adlek). Recibe mensajes de WhatsApp Demos.'
  bot.outgoing_url = url
  bot.save!
  puts "Agent Bot #{created ? 'creado' : 'actualizado'}: id=#{bot.id} (aún NO está conectado a la bandeja)"
  puts 'Siguiente: copia el Token de acceso y el Webhook Secret desde Ajustes → Bots.'
  show_status(account, inbox)

when 'conectar'
  bot = account.agent_bots.find_by!(name: BOT_NAME)
  open_convs = Conversation.where(inbox_id: inbox.id).where.not(status: :resolved)
  puts "Resolviendo #{open_convs.count} conversación(es) no resueltas de la bandeja…"
  open_convs.find_each do |conv|
    puts "  ##{conv.display_id} (#{conv.status}) → resolved"
    conv.update!(status: :resolved)
  end
  link = inbox.agent_bot_inbox || AgentBotInbox.new(inbox: inbox)
  link.agent_bot = bot
  link.status = :active
  link.save!
  puts "Bot '#{bot.name}' conectado a la bandeja #{inbox.name}."
  show_status(account, inbox)

when 'desconectar'
  if inbox.agent_bot_inbox
    inbox.agent_bot_inbox.destroy!
    puts "Bot desconectado de la bandeja #{inbox.name}. Ahora solo la atienden humanos."
  else
    puts 'La bandeja no tenía bot.'
  end
  show_status(account, inbox)

else
  abort "STEP desconocido: #{step}"
end
