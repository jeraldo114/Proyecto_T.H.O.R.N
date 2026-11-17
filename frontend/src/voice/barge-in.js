import { HEARD_AS_THORN, plain } from './wake-word.js';

/** Decide qué hacer con lo que se oyó mientras Thorn respondía. Sin efectos secundarios. */

const PAUSE = new RegExp(`^(?:(?:oye\\s+)?(?:${HEARD_AS_THORN.join('|')})\\s+)?`
  + '(?:espera|para|detente|silencio|alto|pausa|callate|basta)'
  + '(?:\\s+(?:un momento|un segundo|un poco))?(?:\\s+por favor)?$');
// Asentimientos mientras se escucha: no deben cortar la respuesta.
const BACKCHANNEL = new Set(['si', 'ok', 'okay', 'aja', 'mm', 'mmm', 'ajam', 'vale', 'claro', 'bien', 'ya', 'ah', 'eh', 'uh']);

const UNITS = ('cero uno dos tres cuatro cinco seis siete ocho nueve diez once doce trece catorce quince dieciseis '
  + 'diecisiete dieciocho diecinueve veinte veintiuno veintidos veintitres veinticuatro veinticinco veintiseis '
  + 'veintisiete veintiocho veintinueve').split(' ');
const TENS = ',,,treinta,cuarenta,cincuenta,sesenta,setenta,ochenta,noventa'.split(',');
const HUNDREDS = ',ciento,doscientos,trescientos,cuatrocientos,quinientos,seiscientos,setecientos,ochocientos,novecientos'.split(',');

/** El STT escribe «cuarenta» donde la respuesta decía «40»: comparamos en palabras. */
export function numberWords(value) {
  const n = Number(value);
  if (!Number.isInteger(n) || n < 0 || n > 9999) return value;
  if (n < 30) return UNITS[n];
  if (n < 100) return TENS[Math.floor(n / 10)] + (n % 10 ? ` y ${UNITS[n % 10]}` : '');
  if (n === 100) return 'cien';
  if (n < 1000) return HUNDREDS[Math.floor(n / 100)] + (n % 100 ? ` ${numberWords(n % 100)}` : '');
  const thousands = Math.floor(n / 1000);
  return `${thousands === 1 ? '' : `${UNITS[thousands]} `}mil${n % 1000 ? ` ${numberWords(n % 1000)}` : ''}`;
}

export function words(value) {
  return plain(value).replace(/%/g, ' por ciento ').replace(/\d+/g, digits => ` ${numberWords(digits)} `)
    .replace(/[^a-z0-9ñ\s]/g, ' ').split(/\s+/).filter(Boolean);
}

/**
 * La cancelación de eco del navegador no siempre borra la voz de Thorn.
 * Si casi todo lo oído ya estaba en la respuesta, lo tratamos como eco.
 */
export function looksLikeEcho(heard, spoken) {
  const said = words(heard).filter(word => word.length > 2);
  if (!said.length || !spoken) return false;
  const reply = new Set(words(spoken));
  const repeated = said.filter(word => reply.has(word)).length;
  return repeated / said.length >= 0.6;
}

/** 'noise' | 'echo' | 'pause' | 'replace' */
export function classifyInterruption(heard, spoken = '') {
  const tokens = words(heard);
  if (!tokens.length) return 'noise';
  const phrase = tokens.join(' ');
  if (PAUSE.test(phrase)) return 'pause';
  if (tokens.every(word => BACKCHANNEL.has(word))) return 'noise';
  if (looksLikeEcho(heard, spoken)) return 'echo';
  return 'replace';
}
