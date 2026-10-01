// Arcade Cloud Score — ESP32 ligado por USB ao Raspberry Pi: joystick analógico, 3 LEDs e sensor DHT11
// Placa: ESP32 clássico (WROOM-32) · Core ESP32 3.x
// Bibliotecas: "DHT sensor library" e "Adafruit Unified Sensor" (ambas da Adafruit)
//
// Serial a 115200, uma mensagem por linha:
//   ESP32 → Pi:  telemetria em JSON, por exemplo
//                {"temperature":24.0,"humidity":61.0,"x":-120,"y":0,"leds":{"green":120,"yellow":0,"red":0},
//                 "mode":"joystick","playing":false}
//                (linhas começando com "#" são só para o Monitor Serial; o Pi ignora)
//   Pi → ESP32:  "LED green 255" (o site liga/desliga um LED), "MODE joystick", "MODE site",
//                "GAME on" / "GAME off" (jogo aberto no Batocera: os LEDs piscam enquanto ele durar)

#include <Arduino.h>
#include <DHT.h>

// ---------- Pinos ----------
const uint8_t PIN_VRX = 4, PIN_VRY = 15;                 // ADC2: só funciona sem Wi-Fi (aqui ele fica desligado)
const uint8_t LED_VERDE = 21, LED_AMARELO = 22, LED_VERMELHO = 23;
const uint8_t PIN_DHT = 19;

// ---------- Ajustes ----------
const int      ZONA_MORTA      = 250;   // ruído ignorado em torno do centro (escala 0..4095)
const float    SUAVIZACAO      = 0.3;   // 0..1 — menor = mais suave, maior = mais rápido
const uint32_t PWM_FREQ        = 5000;  // Hz
const uint8_t  PWM_RES         = 8;     // brilho de 0 a 255
const uint32_t INTERVALO_ENVIO = 100;   // ms: no máximo 10 mensagens por segundo enquanto o joystick mexe
const uint32_t INTERVALO_DHT   = 2000;  // ms: o DHT11 não aguenta mais de uma leitura por segundo
const uint32_t DURACAO_EFEITO  = 3000;  // ms que cada efeito do modo jogo dura antes de passar para o próximo

const uint8_t LEDS[3]  = {LED_VERDE, LED_AMARELO, LED_VERMELHO};
const char*   CORES[3] = {"green", "yellow", "red"};     // nomes usados nos comandos e no JSON

DHT dht(PIN_DHT, DHT11);

int   centroX, centroY;               // centro calibrado no boot
float fx, fy;                         // leituras filtradas
float temperatura = NAN, umidade = NAN;   // NAN = sensor ainda não leu ou não respondeu
int   brilho[3];                      // o que cada LED está mostrando agora (0..255)
int   site[3];                        // o que o site pediu para cada LED (vale no modo site)
bool  modoSite = false;               // false: LEDs seguem o joystick; true: o site liga e desliga
bool  jogando = false;                // o Pi avisou que há um jogo aberto: a animação tem prioridade

// Média de 64 leituras com o joystick parado
int calibrar(uint8_t pin) {
  long soma = 0;
  for (int i = 0; i < 64; i++) { soma += analogRead(pin); delay(2); }
  return soma / 64;
}

// Converte a leitura em -255..255; cada metade é mapeada separadamente
// porque o centro físico (~1900) não fica no meio exato de 0..4095
int eixo(float leitura, int centro) {
  int v = (int)leitura, d = v - centro;
  if (abs(d) < ZONA_MORTA) return 0;
  if (d < 0) return -map(constrain(v, 0, centro - ZONA_MORTA), centro - ZONA_MORTA, 0, 0, 255);
  return map(constrain(v, centro + ZONA_MORTA, 4095), centro + ZONA_MORTA, 4095, 0, 255);
}

void aplicarLeds() {
  for (int i = 0; i < 3; i++) ledcWrite(LEDS[i], brilho[i]);
}

// Entra no modo site com todos os LEDs apagados (o joystick podia ter deixado algum aceso)
void entrarModoSite() {
  modoSite = true;
  for (int& s : site) s = 0;
}

// Modo jogo: três efeitos que se revezam a cada DURACAO_EFEITO
void animarJogo() {
  uint32_t t = millis();
  switch ((t / DURACAO_EFEITO) % 3) {
    case 0:  // perseguição: verde → amarelo → vermelho, um de cada vez
      for (int i = 0; i < 3; i++) brilho[i] = (t / 150) % 3 == i ? 255 : 0;
      break;
    case 1:  // pisca-pisca: os três juntos
      for (int i = 0; i < 3; i++) brilho[i] = (t / 125) % 2 ? 255 : 0;
      break;
    default:  // onda: cada LED acende e apaga suave, um terço de volta atrás do anterior
      for (int i = 0; i < 3; i++) brilho[i] = 127.5 * (1 + sin(2 * PI * (t / 1000.0 - i / 3.0)));
  }
}

// Aplica um comando do Pi; devolve true se mudou algo
bool executar(const char* cmd) {
  char cor[8];
  int valor;
  if (strcmp(cmd, "MODE joystick") == 0) { modoSite = false; return true; }
  if (strcmp(cmd, "MODE site") == 0) { entrarModoSite(); return true; }
  if (strcmp(cmd, "GAME on") == 0) { jogando = true; return true; }
  if (strcmp(cmd, "GAME off") == 0) { jogando = false; return true; }
  if (sscanf(cmd, "LED %7s %d", cor, &valor) == 2) {
    for (int i = 0; i < 3; i++) {
      if (strcmp(cor, CORES[i]) != 0) continue;
      if (!modoSite) entrarModoSite();
      site[i] = constrain(valor, 0, 255);
      return true;
    }
  }
  Serial.printf("# comando desconhecido: %s\n", cmd);
  return false;
}

// Lê os comandos que chegaram pela serial sem travar o loop; devolve true se algum mudou algo
bool lerComandos() {
  static char linha[48];
  static uint8_t n = 0;
  bool mudou = false;
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\r') continue;
    if (c != '\n') {
      if (n < sizeof linha - 1) linha[n++] = c;
      continue;
    }
    linha[n] = '\0';
    n = 0;
    mudou |= executar(linha);
  }
  return mudou;
}

// Número com uma casa decimal, ou null se o sensor não respondeu
void numeroJson(char* destino, size_t tamanho, float valor) {
  if (isnan(valor)) snprintf(destino, tamanho, "null");
  else snprintf(destino, tamanho, "%.1f", valor);
}

void enviar(int x, int y) {
  char t[12], u[12];
  numeroJson(t, sizeof t, temperatura);
  numeroJson(u, sizeof u, umidade);
  Serial.printf("{\"temperature\":%s,\"humidity\":%s,\"x\":%d,\"y\":%d,"
                "\"leds\":{\"green\":%d,\"yellow\":%d,\"red\":%d},\"mode\":\"%s\",\"playing\":%s}\n",
                t, u, x, y, brilho[0], brilho[1], brilho[2], modoSite ? "site" : "joystick",
                jogando ? "true" : "false");
}

void setup() {
  Serial.begin(115200);
  analogReadResolution(12);
  dht.begin();

  // LEDs com PWM + autoteste (cada um pisca uma vez)
  for (uint8_t p : LEDS) {
    ledcAttach(p, PWM_FREQ, PWM_RES);
    ledcWrite(p, 255); delay(200); ledcWrite(p, 0);
  }

  Serial.println("# Calibrando joystick... nao toque nele.");
  centroX = calibrar(PIN_VRX);
  centroY = calibrar(PIN_VRY);
  fx = centroX; fy = centroY;
  Serial.printf("# Pronto! Centro X=%d Y=%d\n", centroX, centroY);
}

void loop() {
  // Filtro passa-baixa (média móvel exponencial): remove tremidas
  fx += SUAVIZACAO * (analogRead(PIN_VRX) - fx);
  fy += SUAVIZACAO * (analogRead(PIN_VRY) - fy);
  int x = eixo(fx, centroX);
  int y = eixo(fy, centroY);

  bool comando = lerComandos();

  if (jogando) {
    animarJogo();
  } else if (modoSite) {
    for (int i = 0; i < 3; i++) brilho[i] = site[i];
  } else {                        // modo joystick: brilho proporcional à inclinação
    brilho[0] = x < 0 ? -x : 0;   // verde: esquerda
    brilho[1] = abs(y);           // amarelo: cima/baixo
    brilho[2] = x > 0 ?  x : 0;   // vermelho: direita
  }
  aplicarLeds();

  static uint32_t ultimoDht = 0;
  bool leuDht = false;
  if (millis() - ultimoDht >= INTERVALO_DHT) {
    ultimoDht = millis();
    temperatura = dht.readTemperature();
    umidade = dht.readHumidity();
    leuDht = true;
  }

  // Envia quando o joystick mexe (no máximo a cada INTERVALO_ENVIO), logo após um comando
  // e a cada leitura do DHT — que também serve de sinal de vida para o painel
  static uint32_t ultimoEnvio = 0;
  static int ax = 999, ay = 999;
  bool mexeu = x != ax || y != ay;
  if (comando || leuDht || (mexeu && millis() - ultimoEnvio >= INTERVALO_ENVIO)) {
    ultimoEnvio = millis(); ax = x; ay = y;
    enviar(x, y);
  }

  delay(5);
}
