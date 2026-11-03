# Cómo vamos a construir el coordinador de T.H.O.R.N

**Estado: método de trabajo y evaluación inicial preparados.** El coordinador
ampliado descrito aquí todavía no sustituye al runtime. Este documento convierte
la idea de separar interacción, razonamiento y voz en pasos verificables.

**NOTA DEL OPERADOR:** primero quiero que T.H.O.R.N sepa cuándo abrir un panel,
cuándo buscar un recuerdo y cuándo pensar una respuesta. Añadir modelos porque
sí puede gastar más RAM sin resolver nuestros errores.

## 1. Punto de partida: medir lo que existe

Hoy `core/commands.py` reconoce órdenes completas. `core/operator.py` las atiende
antes de consultar a Ollama. El HUD también reconoce algunas órdenes en
`frontend/src/workspace.js` y `frontend/src/network.js`. Los permisos y las
confirmaciones de Windows se comprueban aparte.

La primera evaluación usa **30 ejemplos sintéticos escritos y revisados**:
14 órdenes ya cubiertas, 6 expresiones que queremos incorporar y 10 negativos
con negaciones, citas, preguntas o peticiones compuestas. No proceden del archivo
de conversaciones del operador.

```powershell
.\.venv\Scripts\python.exe tools/evaluate_intents.py
```

El informe queda en `.artifacts/intent-baseline.json`, fuera de Git. Guarda IDs,
etiquetas, tiempos, hashes del corpus/parser y versión de Python; omite el texto
de entrada. `--strict` devuelve un código de
error si alguna etiqueta deseada no coincide. El modo habitual es diagnóstico:
las brechas conocidas no impiden generar el informe.

Resultado inicial del 2 de noviembre de 2026: **24/30 coincidencias**, ninguna
falsa orden en los **10 negativos**, y las 6 expresiones deseadas todavía sin
cobertura. Esto no demuestra un 80 % de precisión en uso real: la muestra es
pequeña, construida a mano y mezcla casos ya conocidos con objetivos nuevos.

El tiempo medido es solo el parser en caliente. No incluye micrófono, STT,
HTTP, búsqueda de recuerdos, carga de modelos, inferencia ni reproducción.

**Entregable:** una línea base versionada por corpus y un informe local reproducible.

## 2. Definir las rutas y sus contratos

El backend será la autoridad sobre la decisión. El HUD podrá anticipar una
cancelación local, pero no conceder permisos ni confirmar una acción. Primero
unificaremos las reglas duplicadas para que hablar y escribir den el mismo resultado.

| Ruta propuesta | Ejemplo | Responsable | Condición |
| --- | --- | --- | --- |
| `control` | «Thorn, espera» | Cancelación prioritaria | Sesión vigente; cancelar tareas y audio propios. |
| `interface` | «Abre mi memoria» | Comando determinista | Permiso de lectura correspondiente. |
| `memory` | «¿Qué acordamos del proyecto?» | Recuperación + LLM local | `memory:read`; citar recuerdos disponibles. |
| `document` | «Resume este PDF» | Documento de la sesión + LLM | Documento seleccionado, permisos y límites. |
| `internet` | «Busca información sobre…» | Servicio web existente | Consentimiento explícito; no adjuntar memoria privada. |
| `os_proposal` | «Pausa la música» | Catálogo de acciones | Preparación, confirmación y auditoría existentes. |
| `reasoning` | «Ayúdame a comparar estas opciones» | LLM local actual | Presupuesto de contexto y una inferencia a la vez. |
| `clarify` | «Ábrelo» sin referente | Pregunta de aclaración | No adivinar un destino ni ejecutar nada. |

Ejemplo del **contrato propuesto**, todavía no un endpoint implementado:

```json
{
  "request_id": "identificador generado por el servidor",
  "route": "interface",
  "intent": "navigate",
  "arguments": {"view": "history"},
  "decision_source": "rule",
  "confidence": null
}
```

El servidor validará claves, tipos, enums, tamaños e IDs del recurso. Resolverá
scopes, propietario y confirmaciones a partir de su catálogo. Esos permisos no
vendrán del modelo. `confidence` será nulo en reglas; un clasificador futuro solo
aportará una puntuación después de calibrarla, sin presentarla como certeza.
Las órdenes compuestas inicialmente pedirán aclaración en lugar de producir
cadenas de acciones. Las citas de un PDF o una página nunca se reenviarán como
una nueva orden del operador.

**Entregable:** tipos y validador del contrato, tabla de rutas y pruebas de permisos.

## 3. Implementar reglas y medir en modo observación

1. Añadir las seis brechas del corpus, revisando negaciones y frases citadas.
2. Centralizar mapas, consultas web y navegación, conservando sus consentimientos.
3. Producir decisiones propuestas sin ejecutarlas desde el coordinador nuevo.
4. Compararlas con el recorrido actual en sesiones de prueba con datos sintéticos.
5. Activar después una ruta a la vez mediante un interruptor de configuración.

El router inicial debería ser una función pura y breve. MySQL, archivos, voz,
red e inferencia seguirán en servicios asíncronos o workers. Una consulta lenta
no debe bloquear la cancelación ni los sensores. Las tareas tendrán propietario,
revisión y cancelación; una respuesta tardía no podrá reactivar un turno detenido.

**Puerta de salida:** 30/30 casos iniciales, cero falsas órdenes en negativos,
pruebas de expiración/cancelación y de órdenes iguales desde voz y teclado.
Pasar esta muestra permite avanzar; no equivale a validar todo el lenguaje.

## 4. Ampliar el conjunto de evaluación

Preparar al menos 300 ejemplos revisados para la siguiente evaluación, cubriendo
cada intención, variantes latinoamericanas, errores de dictado, frases incompletas,
negación, citas, ambigüedad y destinos inexistentes. Registrar para cada ejemplo
la familia de paráfrasis y el origen de generación.

Separar entrenamiento, validación y prueba por familias de paráfrasis, manteniendo
representación de las intenciones en cada partición. No repartir copias casi
idénticas entre ellas. Congelar la prueba antes de ajustar reglas o modelos.
Cada fallo añadido después será una regresión, con su ID y explicación.

No llamar «prueba independiente» a un corpus usado repetidamente para ajustar.
Los datos sintéticos también requieren revisión humana: su etiqueta puede estar mal.

| Medida | Criterio propuesto para avanzar |
| --- | --- |
| Precisión por intención y matriz de confusión | Reportar clases por separado; objetivo inicial ≥95 % macro-F1 en prueba independiente. |
| Falsas órdenes en casos negativos | Cero en el conjunto de aceptación revisado; cualquier fallo obliga a investigar. |
| Confirmaciones y scopes | Ninguna acción ejecutada saltándose los controles, incluso con una clasificación errónea. |
| Abstención | Medir cuántas órdenes resuelve y cuántas deriva a aclaración; no mejorar seguridad rechazándolo todo. |
| Latencia del router | Medir p50/p95 en el F15; objetivo inicial de reglas: p95 <50 ms, separado del resto del turno. |
| Recursos | Registrar RAM/VRAM y CPU en reposo y durante voz + IA; comparar con la línea base. |

Son objetivos de aceptación, no resultados ya alcanzados ni garantías fuera de
las muestras. Además del parser hay que medir desde el fin de voz hasta el primer
audio, con motor frío/caliente y carga concurrente. No confundiremos estos tiempos.

## 5. Decidir si hace falta un modelo pequeño

Solo añadirlo si los errores muestran que las reglas no cubren la variación del
lenguaje de manera mantenible. Comparar tres candidatos sobre la misma prueba:
reglas, clasificador pequeño especializado y el LLM actual con salida restringida.

El modelo de interacción predecirá intención y argumentos; no generará shell,
coordenadas arbitrarias del escritorio ni permisos. Si no entiende, se abstendrá.
Elegiremos el candidato por utilidad medida y recursos, no por su número de modelos.

Si se entrena, será una especialización de una base existente con procedencia y
licencia revisadas. Guardaremos versión de datos, configuración, semilla, pesos y
reporte. No lo describiremos como un modelo entrenado desde cero.

Las conversaciones privadas siguen siendo memoria del operador. Usarlas como
entrenamiento exige una decisión específica y una exportación revisada; guardar
conversaciones no activa aprendizaje automático. Los corpus compartidos en Git
seguirán siendo sintéticos y sin claves ni datos personales.

**Entregable:** comparación reproducible y decisión justificada. Mantener reglas
también será una decisión válida si resuelven mejor el problema en el F15.

## 6. Integrar razonamiento y voz por separado

El razonamiento seguirá usando el motor local actual al principio. Un modelo
mayor solo se incorporará si mejora tareas concretas y cabe en el presupuesto
real de RAM/VRAM. No mantendremos dos modelos residentes por defecto sin medirlo.

La voz tendrá una comparación propia: mismo texto, acento, pronunciación de
siglas y números, tiempo hasta el primer audio y consumo. La elección de una voz
no quedará atada al clasificador. Prepararemos muestras para escuchar antes de
sustituir el proveedor habitual.

Para respuestas progresivas habrá que adaptar el contrato JSON actual de Ollama,
validar lo que pueda presentarse y serializar fragmentos de TTS. Las acciones
nunca se ejecutarán a partir de JSON parcial. Cancelar invalidará la revisión,
vaciará la cola de voz y descartará resultados pendientes. El historial solo
marcará un turno completo al confirmarlo como tal.

**Entregable:** una conversación completa, cancelable y medida; latencia y recursos
comparados con la versión estable, además de una evaluación auditiva del operador.

## 7. Entrega y vuelta atrás

Una ruta se activará después de sus pruebas unitarias, contratos HTTP y flujo
real del HUD. Verificaremos permisos, cancelación, ausencia de solicitudes externas
inesperadas y recuperación de memoria tras reconectar. Después se comprobarán
el ejecutable, sus recursos y el paquete firmado.

Conservaremos la versión estable y un interruptor para volver al recorrido
anterior. No cambiaremos la clave de datos, el formato de cifrado ni las carpetas
del perfil como parte de este trabajo. La publicación en GitHub seguirá pasando
por las comprobaciones y protecciones del repositorio.

## Orden de trabajo

1. **Preparación lista:** método, corpus inicial y evaluador del parser actual.
2. **Primera implementación:** contrato único, las seis brechas y modo observación.
3. **Evaluación ampliada:** corpus independiente, permisos y tiempos del turno completo.
4. **Especialización si aporta:** clasificador pequeño medido frente a las reglas.
5. **Experiencia de voz:** elegir voz con muestras, después salida progresiva y cancelación.

Las fechas dependerán de los resultados. No hace falta entrenar tres modelos
para empezar a construir un coordinador útil.


## Estado de la implementación

El chat ya pasa por un coordinador tipado y las seis frases pendientes están
cubiertas por la evaluación estricta. Los controles que deben funcionar con
una consulta en vuelo mantienen su vía inmediata desde el HUD. PDF y red
conservan sus rutas privadas y sus permisos independientes.

La línea base de 24/30 queda como comparación histórica. El conjunto de 30
casos sirve para detectar regresiones. El corpus ampliado y una comparación
contra un clasificador entrenado quedan pendientes. Antes de cambiar la voz,
quiero escuchar las mismas frases con ambas opciones y medir el tiempo hasta
que sale el primer audio.

La búsqueda del cuaderno usa un diccionario local de conceptos sobre los
índices HMAC existentes. Proyectos, preferencias y tareas se etiquetan por
expresiones del turno. Son categorías orientativas. Para borrar un tema,
búscalo, revisa las coincidencias y selecciona qué entradas quieres quitar.
La pausa afecta a nuevos turnos del archivo, y el contexto temporal sigue
disponible durante la sesión.
