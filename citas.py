import sys
import os
import logging
import io
import base64
import json
import requests
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler

from flask import Flask, Response, render_template, request, redirect, url_for, flash, session, jsonify
from dotenv import load_dotenv


import oracledb


sys.modules["cx_Oracle"] = oracledb

import cx_Oracle

load_dotenv()

app = Flask(__name__, template_folder='templates')

app.secret_key = os.getenv('APP_SECRET_KEY')

# Directorio donde se guardarán los logs.
log_folder = 'log'
if not os.path.exists(log_folder):
    os.makedirs(log_folder)

loggers = {}

def configurar_log(nombre_api, forzar_logs=False):
    """
    Configura y devuelve un logger con un FileHandler para la API especificada.
    El nombre del archivo de log incluye la fecha actual.
    """
    fecha_actual = datetime.now().strftime("%Y-%m-%d")
    log_filename = os.path.join(log_folder, f"{nombre_api}_{fecha_actual}.log")

    if nombre_api in loggers:
        logger = loggers[nombre_api]
        for handler in logger.handlers:
            if isinstance(handler, logging.FileHandler) and handler.baseFilename == os.path.abspath(log_filename):
                return logger
        
        for handler in logger.handlers[:]:
            handler.close()
            logger.removeHandler(handler)
    else:
        logger = logging.getLogger(nombre_api)
        logger.setLevel(logging.DEBUG)
        loggers[nombre_api] = logger


    handler = logging.FileHandler(log_filename, encoding='utf-8')
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    
    logger.info("Logs habilitados permanentemente para el endpoint '%s'.", nombre_api)
    return logger
def obtener_conexion_produccion():
    """Conexión a la base de datos de PRODUCCIÓN y verificación de esquema."""
    try:
        dsn = cx_Oracle.makedsn(
            os.getenv('PROD_HOST'),
            os.getenv('PROD_PORT'),
            service_name=os.getenv('PROD_DATABASE')
        )
        
        conexion = cx_Oracle.connect(
            user=os.getenv('PROD_USER'),
            password=os.getenv('PROD_PASSWORD'),
            dsn=dsn
        )

        cursor = conexion.cursor()
        cursor.execute("SELECT sys_context('USERENV', 'CURRENT_SCHEMA') FROM dual")
        esquema_actual = cursor.fetchone()[0]
        cursor.close()

        logging.info(f"✅ Conexión establecida.")
        logging.info(f"🔍 Esquema actual: {esquema_actual}")
        logging.info(f"🔍 Host: {os.getenv('PROD_HOST')} | Service: {os.getenv('PROD_DATABASE')}")
        print(f"✅ Conexión a PRODUCCIÓN exitosa. Esquema actual: {esquema_actual}")
        return conexion
        
    except Exception as e:
        logging.error(f"🔴 Error al conectar a la DB de producción: {e}")
        raise


@app.route('/')
def inicio():
    return render_template('inicio_citas.html')

@app.route('/login', methods=['POST'])
def login():
    usuario = request.form.get('logemail', '').strip()
    clave = request.form.get('logpass', '').strip()
    sede = request.form.get('opciones')
    
    print("Usuario recibido:", usuario)
    print("Clave recibida:", clave)

    conexion = None
    cursor = None
    try:
        conexion = obtener_conexion_produccion()
        cursor = conexion.cursor()

        cursor.execute(
            "SELECT * FROM F58USERS WHERE TRIM(USUSER) = :usuario AND TRIM(USSECV3) = :clave",
            {"usuario": usuario, "clave": clave}
        )
        
        usuario_db = cursor.fetchone()
        print("Resultado de la consulta:", usuario_db)

        if usuario_db:
            session['usuario'] = usuario
            session['sede'] = sede
            flash('Inicio de sesión exitoso', 'success')
            print("✅ Usuario autenticado correctamente")
            return redirect(url_for('morbilidad'))
        else:
            flash('Usuario o contraseña incorrectos', 'error')
            return redirect(url_for('inicio'))

    except Exception as e:
        print("Error en login:", e)
        flash('Ocurrió un error. Intenta nuevamente.', 'error')
        return redirect(url_for('inicio'))
    finally:
        if cursor: cursor.close()
        if conexion: conexion.close()
            
@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('inicio'))

@app.route('/morbilidad')
def morbilidad():
    if 'usuario' not in session:
        return redirect(url_for('inicio'))
    return render_template('morbilidad.html')

@app.route('/validar_preempleo', methods=['POST'])
def validar_preempleo():
    clave = request.json.get('clave')
    conexion = None
    cursor = None
    try:
        conexion = obtener_conexion_produccion()
        cursor = conexion.cursor()

        cursor.execute("SELECT 1 FROM EM_PREEMPLEOS WHERE NUMEROPREEMPLEO = :clave", {"clave": clave})
        resultado = cursor.fetchone()
        return jsonify({"acceso": bool(resultado)})
    except Exception as e:
        print(f"🔴 Error en validar_preempleo: {e}")
        return jsonify({"acceso": False}), 500
    finally:
        if cursor: cursor.close()
        if conexion: conexion.close()

@app.route('/validar_historia', methods=['POST'])
def validar_historia():
    """
    Valida si una clave existe en EM_CONSULTAS buscando tanto por 
    NUMEROCONSULTA como por NUMEROHISTORIA.
    
    Retorna:
    - {"acceso": True, "tipo": "consulta"} si coincidió con el número de consulta.
    - {"acceso": True, "tipo": "historia"} si coincidió con el número de historia.
    - {"acceso": False} si no se encuentra.
    """
    clave = request.json.get('clave')
    conexion = None
    cursor = None
    
    if not clave:
        return jsonify({"acceso": False, "error": "Clave no proporcionada"}), 400

    try:
        conexion = obtener_conexion_produccion()
        cursor = conexion.cursor()

        print(f"🔍 Evaluando clave {clave} en EM_CONSULTAS...")

        query = """
            SELECT NUMEROCONSULTA, NUMEROHISTORIA 
            FROM EM_CONSULTAS 
            WHERE NUMEROCONSULTA = :clave OR NUMEROHISTORIA = :clave
        """
        cursor.execute(query, {"clave": clave})
        resultado = cursor.fetchone()

        if resultado:
            num_consulta, num_historia = resultado
            
            clave_str = str(clave).strip()

            if str(num_consulta).strip() == clave_str:
                print(f"✅ Clave {clave} encontrada como NUMEROCONSULTA.")
                return jsonify({"acceso": True, "tipo": "consulta"})
                
            elif str(num_historia).strip() == clave_str:
                print(f"✅ Clave {clave} encontrada como NUMEROHISTORIA.")
                return jsonify({"acceso": True, "tipo": "historia"})

        print(f"🚫 Clave {clave} no existe en EM_CONSULTAS.")
        return jsonify({"acceso": False})
        
    except Exception as e:
        print(f"🔴 Error en validar_historia para clave {clave}: {e}", file=sys.stderr)
        return jsonify({"acceso": False, "error_detail": str(e)}), 500
    finally:
        if cursor: cursor.close()
        if conexion: conexion.close()

    
@app.route('/obtener_nombre_contratante', methods=['POST'])
def obtener_nombre_contratante():
    conexion = None
    cursor = None
    try:
        data = request.get_json()
        contrato = data.get('contrato', '').strip()

        if not contrato:
            return jsonify({'error': 'Contrato no proporcionado.'}), 400

        conexion = obtener_conexion_produccion()
        cursor = conexion.cursor()

        sql = """
            SELECT RPNOMBR 
            FROM F58AF001 
            WHERE TRIM(UPPER(RPNUEX)) = TRIM(UPPER(:contrato))
        """

        cursor.execute(sql, contrato=contrato)
        result = cursor.fetchone()

        if result:
            return jsonify({'nombre_contratante': result[0].strip()}), 200
        else:
            return jsonify({'error': 'Nombre no encontrado'}), 404

    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor: cursor.close()
        if conexion: conexion.close()

@app.route('/obtener_datos', methods=['POST'])
def obtener_datos():
    """
    Obtiene la cédula, contratante, nombres del afiliado/contratante,
    y las enfermedades/limitante guardadas para una clave y tipo específicos
    ('preempleo' o 'historia').

    AJUSTE: Centraliza la consulta en EM_HISTORIAS (en lugar de EM_CONSULTAS)
    y realiza la migración al vuelo desde EM_HISTORIAS_MORBILIDAD por NUMEROHISTORIA.
    """
    import sys

    # Mapeo: Columna Vieja (EM_HISTORIAS_MORBILIDAD) -> ID Enfermedad Nueva
    MAPEO_MORBILIDAD_VIEJA = {
        "C01APARSANO": 130, "C04ALCOHOLISMO": 74, "C06ASMABRON": 14,
        "C08CANCTODASUSFORM": 82, "C09CARDISQU": 11, "C11CARIES": 61,
        "C12CIFOSIS": 101, "C13COLOPATIA": 66, "C14CONJUNTIVITIS": 51,
        "C15DERMATOMICOSIS": 31, "C16DIABMIEL": 25, "C18EMBARAZO": 90,
        "C19ENFECEREVASC": 8, "C20ENFEPEPT": 63, "C21ESCOLIOSIS": 100,
        "C22FRACTURA": 45, "C23HEMORROIDES": 9, "C24HEPATITIS": 77,
        "C28HIPEARTE": 13, "C30HIPERTIROIDISMO": 29, "C31HIPOTIROIDISMO": 28,
        "C33INFEURIN": 87, "C34INSURENA": 23, "C35LITIRENA": 97,
        "C36LITIVESI": 106, "C38OBESIDAD": 27, "C39ONICOMICOSIS": 32,
        "C41OTITIS": 57, "C46SINDCONV": 2, "C49TABAQUISMO": 127,
        "C50VARICES": 10, "C51VICIDEREFRVISU": 52
    }

    data = request.json or {}
    clave = data.get('clave')
    tipo = data.get('tipo')  

    if not clave or not tipo:
        return jsonify({"error": "Faltan datos de clave o tipo"}), 400

    print("--- Proceso de Obtención de Datos ---")
    print(f"Tipo de búsqueda: {tipo} | Clave recibida: {clave}")

    conexion_produccion = None
    cursor_produccion = None
    
    cedula = None
    contratante = None
    contrato = None
    enfermedades_guardadas = []
    limitante_guardado = 0 
    nombre_afiliado = "No encontrado"
    apellido_afiliado = "No encontrado"
    nombre_contratante = "No encontrado"

    try:
        
        if tipo == 'preempleo':
            tipo_corto = "P"
            tabla_principal = "EM_PREEMPLEOS"
            campo_clave = "NUMEROPREEMPLEO"
            
            query_afiliado_tabla = "CT_AFILIADOSAUX"
            query_afiliado_campos = "AFILIADO"
            afiliado_campo_cedula = "CEDULA"
            
            query_contratante_tabla = "CT_AFILIADOSAUX"
            query_contratante_campo_ref = "CICONTRATANTE"
            contratante_campo_nombre = "CONTRATANTE"
            
        elif tipo in ['historia', 'consulta']:

            tipo_corto = "H" if tipo == 'historia' else "C"
            tabla_principal = "EM_HISTORIAS"
            campo_clave = "NUMEROHISTORIA"

            query_afiliado_tabla = "F58AF005"
            query_afiliado_campos = "BFNOMBR, BFAPELL"
            afiliado_campo_cedula = "BFCIRIF"
            
            query_contratante_tabla = "F58AF001"
            query_contratante_campo_ref = "RPCIRIF"
            contratante_campo_nombre = "RPNOMBR"
        else:
            return jsonify({"error": "Tipo inválido. Debe ser 'preempleo' o 'historia'"}), 400

        # 2. Conexión y consulta del registro base en EM_HISTORIAS / EM_PREEMPLEOS
        conexion_produccion = obtener_conexion_produccion() 
        cursor_produccion = conexion_produccion.cursor()
        
        sql_query_principal = f"""
            SELECT CEDULA, CONTRATANTE, CONTRATO 
            FROM {tabla_principal} 
            WHERE {campo_clave} = :clave
        """
        cursor_produccion.execute(sql_query_principal, {"clave": clave})
        resultado_principal = cursor_produccion.fetchone()
        
        if not resultado_principal:
            print(f"🚫 Registro no encontrado en {tabla_principal} con clave {clave}.")
            return jsonify({"error": f"Registro no encontrado en {tabla_principal}"}), 404
        
        cedula, contratante, contrato = resultado_principal
        contrato_normalizado = str(contrato).strip().upper() if contrato else None
        
        # 2.1 Buscar nombre y apellido del afiliado
        cedula_normalizada = cedula.strip().upper() if cedula else ""
        if cedula_normalizada and afiliado_campo_cedula:
            query_afiliado = f"""
                SELECT {query_afiliado_campos} 
                FROM {query_afiliado_tabla} 
                WHERE TRIM({afiliado_campo_cedula}) = :cedula_normalizada
            """
            cursor_produccion.execute(query_afiliado, {"cedula_normalizada": cedula_normalizada})
            res_afiliado = cursor_produccion.fetchone()
            
            if res_afiliado:
                if tipo == 'preempleo':
                    nombre_completo = res_afiliado[0].strip()
                    last_space = nombre_completo.rfind(' ')
                    if last_space != -1:
                        nombre_afiliado = nombre_completo[:last_space].strip()
                        apellido_afiliado = nombre_completo[last_space + 1:].strip()
                    else:
                        nombre_afiliado = nombre_completo
                        apellido_afiliado = ""
                else:
                    nombre_afiliado = res_afiliado[0].strip() if res_afiliado[0] else ""
                    apellido_afiliado = res_afiliado[1].strip() if res_afiliado[1] else ""

        # 2.2 Buscar nombre del contratante
        contratante_normalizado = contratante.strip().upper() if contratante else ""
        if contratante_normalizado and query_contratante_campo_ref:
            query_contratante = f"""
                SELECT {contratante_campo_nombre} 
                FROM {query_contratante_tabla} 
                WHERE TRIM({query_contratante_campo_ref}) = :contratante_normalizado
            """
            cursor_produccion.execute(query_contratante, {"contratante_normalizado": contratante_normalizado})
            res_contratante = cursor_produccion.fetchone()
            if res_contratante and res_contratante[0]:
                nombre_contratante = res_contratante[0].strip()

        # 3. Consultar morbilidad guardada en nuevo esquema (EM_HISTORIA_MORBILIDAD)
        print(f"✅ Buscando morbilidad en esquema nuevo (TIPO='{tipo_corto}', NUMERO='{clave}').")
        
        cursor_produccion.execute("""
            SELECT T3.CODIGO, T1.LIMITANTE 
            FROM EM_HISTORIA_MORBILIDAD T1 
            LEFT JOIN EM_HISTORIA_MORBILIDAD_DETALLE T2 ON T1.ID_HIST_MORB = T2.ID_HIST_MORB
            LEFT JOIN EM_ENFERMEDADES T3 ON T2.CODIGO_ENF = T3.CODIGO
            WHERE T1.TIPO = :p_tipo AND T1.NUMERO = :p_numero
        """, {"p_tipo": tipo_corto, "p_numero": clave})
        
        enfermedades_db_con_limitante = cursor_produccion.fetchall()

        if enfermedades_db_con_limitante and any(row[0] is not None for row in enfermedades_db_con_limitante):
            limitante_guardado = enfermedades_db_con_limitante[0][1] if enfermedades_db_con_limitante[0][1] is not None else 0
            enfermedades_guardadas = [enf[0] for enf in enfermedades_db_con_limitante if enf[0] is not None]
        else:

            if tipo in ['historia', 'consulta']:
                print(f"⚠️ Morbilidad nueva vacía. Migrando antecedentes de EM_HISTORIAS_MORBILIDAD para NUMEROHISTORIA: {clave}...")
                
                columnas_viejas = ", ".join(MAPEO_MORBILIDAD_VIEJA.keys())
                sql_tabla_vieja = f"""
                    SELECT {columnas_viejas}
                    FROM EM_HISTORIAS_MORBILIDAD
                    WHERE NUMEROHISTORIA = :num_historia
                """
                cursor_produccion.execute(sql_tabla_vieja, {"num_historia": clave})
                registro_viejo = cursor_produccion.fetchone()
                
                if registro_viejo:
                    columnas_db = [col[0] for col in cursor_produccion.description]
                    valores_mapeados = dict(zip(columnas_db, registro_viejo))
                    
                    for columna, valor in valores_mapeados.items():
                        columna_upper = columna.upper()
                        valor_str = str(valor).strip() if valor is not None else "0"
                        
                        if (valor_str == "1" or valor_str.startswith("1.")) and MAPEO_MORBILIDAD_VIEJA.get(columna_upper):
                            enfermedades_guardadas.append(MAPEO_MORBILIDAD_VIEJA[columna_upper])
                    
                    print(f"✅ Migración al vuelo extraída: {enfermedades_guardadas}")

                    if enfermedades_guardadas:
                        try:
                            print(f"💾 Persistiendo migración en EM_HISTORIA_MORBILIDAD (TIPO: {tipo_corto}, NUMERO: {clave})...")
                            cursor_produccion.execute("SELECT SEQ_EM_HISTORIA_MORBILIDAD.NEXTVAL FROM DUAL")
                            nuevo_id_morb = cursor_produccion.fetchone()[0]
                            
                            cursor_produccion.execute("""
                                INSERT INTO EM_HISTORIA_MORBILIDAD (ID_HIST_MORB, TIPO, NUMERO, USUARIO, LIMITANTE, FECHA)
                                VALUES (:p_id, :p_tipo, :p_numero, 'MIGRACION_AUTO', 0, SYSDATE)
                            """, {
                                "p_id": nuevo_id_morb,
                                "p_tipo": tipo_corto,
                                "p_numero": clave
                            })
                            
                            for cod_enf in enfermedades_guardadas:
                                cursor_produccion.execute("""
                                    INSERT INTO EM_HISTORIA_MORBILIDAD_DETALLE (ID_HIST_MORB, CODIGO_ENF)
                                    VALUES (:p_id, :p_codigo)
                                """, {"p_id": nuevo_id_morb, "p_codigo": cod_enf})
                                
                            conexion_produccion.commit()
                            print(f"🔒 Historia {clave} guardada exitosamente en el nuevo modelo.")
                        except Exception as ex_migracion:
                            conexion_produccion.rollback()
                            print(f"🔴 Error al persistir migración automática de {clave}: {ex_migracion}", file=sys.stderr)

        print(f"✅ Enfermedades obtenidas: {enfermedades_guardadas}")
        print(f"✅ Estado de Limitante: {limitante_guardado}")
        print("--- Proceso de Obtención Finalizado ---")

        return jsonify({
            "clave": clave, 
            "cedula": cedula,
            "nombre_afiliado": nombre_afiliado,
            "apellido_afiliado": apellido_afiliado,
            "contratante": contratante,
            "nombre_contratante": nombre_contratante,
            "contrato": contrato_normalizado, 
            "enfermedades_guardadas": enfermedades_guardadas,
            "limitante_guardado": limitante_guardado 
        })

    except Exception as e:
        print(f"🔴 Error general en obtener_datos: {e}", file=sys.stderr)
        return jsonify({"error": "Error interno del servidor", "detail": str(e)}), 500
    finally:
        if cursor_produccion: 
            cursor_produccion.close()
        if conexion_produccion: 
            conexion_produccion.close()
        
@app.route("/get_enfermedades_y_categorias")
def get_enfermedades_y_categorias():
    conexion = None
    cursor = None
    try:
        conexion = obtener_conexion_produccion()
        cursor = conexion.cursor()
        
        
        cursor.execute("""
            SELECT 
                c.DESCRIPCION AS categoria, 
                e.CODIGO AS codigo_enfermedad, 
                e.DESCRIPCION AS enfermedad
            FROM EM_CATEGORIAS c
            LEFT JOIN EM_ENFERMEDADES e ON c.CODIGO = e.CODIGO_CATEGORIA
            ORDER BY c.CODIGO, e.DESCRIPCION
        """)
        datos = cursor.fetchall()
        
        enfermedades_agrupadas = {}
        
        for categoria, codigo, enfermedad in datos:
            if categoria not in enfermedades_agrupadas:
                enfermedades_agrupadas[categoria] = []
            if enfermedad and codigo:

                enfermedades_agrupadas[categoria].append({"codigo": codigo, "descripcion": enfermedad.strip()})
        
        return jsonify(enfermedades_agrupadas)
    except Exception as e:
        print(f"🔴 Error en get_enfermedades_y_categorias: {e}")
        return jsonify({"error": "Error interno del servidor"}), 500
    finally:
        if cursor: cursor.close()
        if conexion: conexion.close()

@app.route('/cierre_emision', methods=['GET'])
def cierre_emision():
    """
    Endpoint con Server-Sent Events (SSE) para procesar el cierre de emisión
    en tiempo real y transmitir el avance a la consola JS.
    """

    fecha_inicio = request.args.get('fecha_inicio') 
    fecha_fin = request.args.get('fecha_fin')
    
    usuario = session.get('usuario', 'MIGRACION_DIARIA')

    MAPEO_MORBILIDAD_VIEJA = {
        "C01APARSANO": 130, "C04ALCOHOLISMO": 74, "C06ASMABRON": 14,
        "C08CANCTODASUSFORM": 82, "C09CARDISQU": 11, "C11CARIES": 61,
        "C12CIFOSIS": 101, "C13COLOPATIA": 66, "C14CONJUNTIVITIS": 51,
        "C15DERMATOMICOSIS": 31, "C16DIABMIEL": 25, "C18EMBARAZO": 90,
        "C19ENFECEREVASC": 8, "C20ENFEPEPT": 63, "C21ESCOLIOSIS": 100,
        "C22FRACTURA": 45, "C23HEMORROIDES": 9, "C24HEPATITIS": 77,
        "C28HIPEARTE": 13, "C30HIPERTIROIDISMO": 29, "C31HIPOTIROIDISMO": 28,
        "C33INFEURIN": 87, "C34INSURENA": 23, "C35LITIRENA": 97,
        "C36LITIVESI": 106, "C38OBESIDAD": 27, "C39ONICOMICOSIS": 32,
        "C41OTITIS": 57, "C46SINDCONV": 2, "C49TABAQUISMO": 127,
        "C50VARICES": 10, "C51VICIDEREFRVISU": 52
    }

    def generar_eventos():
        conexion = None
        cursor = None
        registros_migrados = 0
        registros_omitidos = 0

        def emitir_evento(status, msg, procesadas=0, omitidas=0):
            payload = {
                "status": status,
                "msg": msg,
                "procesadas": procesadas,
                "omitidas": omitidas
            }
            return f"data: {json.dumps(payload)}\n\n"

        try:
            if not fecha_inicio or not fecha_fin:
                yield emitir_evento("error", "No se especificó el rango de fechas objetivo.")
                return

            yield emitir_evento("info", f"⚡ Conectando con la base de datos para el rango: {fecha_inicio} a {fecha_fin}...")
            
            conexion = obtener_conexion_produccion()
            cursor = conexion.cursor()

         
            query_citas = """
                SELECT 
                    TRIM(c.CEDULA) AS CEDULA,
                    (
                        SELECT h.NUMEROHISTORIA
                        FROM (
                            SELECT NUMEROHISTORIA 
                            FROM EM_HISTORIAS 
                            WHERE TRIM(CEDULA) = TRIM(c.CEDULA) 
                              AND NUMEROHISTORIA IS NOT NULL 
                            ORDER BY FECHA DESC
                        ) h
                        WHERE ROWNUM = 1
                    ) AS NUMEROHISTORIA,
                    (
                        SELECT p.NUMEROPREEMPLEO
                        FROM (
                            SELECT NUMEROPREEMPLEO 
                            FROM EM_PREEMPLEOS 
                            WHERE TRIM(CEDULA) = TRIM(c.CEDULA) 
                              AND NUMEROPREEMPLEO IS NOT NULL 
                            ORDER BY FECHA DESC
                        ) p
                        WHERE ROWNUM = 1
                    ) AS NUMEROPREEMPLEO,
                    TO_CHAR(c.FECHA, 'YYYY-MM-DD') AS FECHA_CITA
                FROM CT_CITAS c
                LEFT JOIN CT_TURNOS tur ON c.ID_TURNO = tur.ID_TURNO
                LEFT JOIN CT_STATUS st ON c.ID_STATUS = st.ID_STATUS
                WHERE c.FECHA >= TO_DATE(:fecha_inicio, 'YYYY-MM-DD')
                  AND c.FECHA < TO_DATE(:fecha_fin, 'YYYY-MM-DD') + 1
                  AND UPPER(TRIM(st.DESCRIPCION)) = 'ATENDIDO'
                  AND UPPER(TRIM(tur.DESCRIPCION)) = 'MAÑANA 1ER TURNO'
                ORDER BY c.FECHA ASC
            """

            cursor.execute(query_citas, {"fecha_inicio": fecha_inicio, "fecha_fin": fecha_fin})
            citas = cursor.fetchall()

            total_encontrados = len(citas)
            yield emitir_evento("info", f"📋 Pacientes encontrados para procesar: {total_encontrados}")

            if total_encontrados == 0:
                yield emitir_evento("final", "Proceso completado sin registros por procesar.", 0, 0)
                return

            columnas_viejas = ", ".join(MAPEO_MORBILIDAD_VIEJA.keys())

            for i, row in enumerate(citas, 1):
                v_cedula = row[0]
                v_num_historia = row[1]
                v_num_preempleo = row[2]
                v_fecha_cita = row[3] # <--- Fecha exacta de esta cita

                numero_identificador = str(v_num_historia).strip() if v_num_historia else (str(v_num_preempleo).strip() if v_num_preempleo else None)
                tipo_registro = 'H' if v_num_historia else ('P' if v_num_preempleo else None)

                if not numero_identificador:
                    yield emitir_evento("progress", f"⚠️ [{i}/{total_encontrados}] CI: {v_cedula} sin N° de Historia ni Preempleo. Omitiendo.")
                    registros_omitidos += 1
                    continue

              
                cursor.execute("""
                    SELECT 1 FROM EM_HISTORIA_MORBILIDAD 
                    WHERE TIPO = :tipo AND NUMERO = :numero 
                      AND TRUNC(FECHA) = TO_DATE(:fecha, 'YYYY-MM-DD')
                """, {
                    "tipo": tipo_registro, 
                    "numero": numero_identificador, 
                    "fecha": v_fecha_cita
                })

                if cursor.fetchone():
                    yield emitir_evento("progress", f"⏭️ [{i}/{total_encontrados}] {tipo_registro}-{numero_identificador} ya fue procesado. Saltando.")
                    registros_omitidos += 1
                    continue

                sql_tabla_vieja = f"""
                    SELECT {columnas_viejas}
                    FROM EM_HISTORIAS_MORBILIDAD
                    WHERE NUMEROHISTORIA = :num_historia
                """
                cursor.execute(sql_tabla_vieja, {"num_historia": numero_identificador})
                registro_viejo = cursor.fetchone()

                enfermedades_detectadas = []

                if registro_viejo:
                    columnas_db = [col[0] for col in cursor.description]
                    valores_mapeados = dict(zip(columnas_db, registro_viejo))

                    for col, val in valores_mapeados.items():
                        val_str = str(val).strip() if val is not None else "0"
                        if (val_str == "1" or val_str.startswith("1.")) and MAPEO_MORBILIDAD_VIEJA.get(col.upper()):
                            enfermedades_detectadas.append(MAPEO_MORBILIDAD_VIEJA[col.upper()])

                cursor.execute("SELECT PRODDTA.SEQ_EM_HISTORIA_MORBILIDAD.NEXTVAL FROM DUAL")
                nuevo_id_morb = cursor.fetchone()[0]

 
                cursor.execute("""
                    INSERT INTO EM_HISTORIA_MORBILIDAD (ID_HIST_MORB, TIPO, NUMERO, USUARIO, LIMITANTE, FECHA)
                    VALUES (:p_id, :p_tipo, :p_numero, :p_usuario, 0, TO_DATE(:p_fecha, 'YYYY-MM-DD'))
                """, {
                    "p_id": nuevo_id_morb,
                    "p_tipo": tipo_registro,
                    "p_numero": numero_identificador,
                    "p_usuario": usuario,
                    "p_fecha": v_fecha_cita
                })

                for cod_enf in enfermedades_detectadas:
                    cursor.execute("""
                        INSERT INTO EM_HISTORIA_MORBILIDAD_DETALLE (ID_HIST_MORB, CODIGO_ENF)
                        VALUES (:p_id, :p_codigo)
                    """, {"p_id": nuevo_id_morb, "p_codigo": cod_enf})

                registros_migrados += 1
                yield emitir_evento("progress", f"✅ [{i}/{total_encontrados}] Migrado {tipo_registro}-{numero_identificador} (Patologías: {len(enfermedades_detectadas)}).")

            conexion.commit()
            yield emitir_evento("final", "Sincronización diaria finalizada con éxito.", registros_migrados, registros_omitidos)

        except Exception as e:
            if conexion: 
                conexion.rollback()
            print(f"🔴 Error SSE Cierre Emisión: {e}", file=sys.stderr)
            yield emitir_evento("error", f"Error en servidor: {str(e)}")
        finally:
            if cursor: cursor.close()
            if conexion: conexion.close()

    return Response(generar_eventos(), mimetype='text/event-stream')


@app.route("/guardar_morbilidad", methods=["POST"])
def guardar_morbilidad():
    """
    Guarda y actualiza la morbilidad de un registro (preempleo, historia o consulta), 
    incluyendo el estado del campo LIMITANTE (0 o 1).

    Proceso:
    1. Valida el usuario, tipo (P, H, C) y la clave.
    2. Conecta a la base de datos de PRODUCCIÓN.
    3. Busca un registro existente en EM_HISTORIA_MORBILIDAD por TIPO y NUMERO.
    4. Si existe, actualiza el LIMITANTE y el USUARIO.
    5. Si no existe, genera un nuevo ID de secuencia e inserta el nuevo registro con LIMITANTE.
    6. Elimina todos los detalles anteriores de EM_HISTORIA_MORBILIDAD_DETALLE.
    7. Inserta los nuevos códigos de enfermedad.
    8. Realiza COMMIT.
    """

    try:
        logger = configurar_log('guardar_morbilidad')
    except NameError:
        # Fallback si configurar_log no está definida
        import logging
        logger = logging.getLogger('guardar_morbilidad')
        logging.basicConfig(level=logging.INFO)


    logger.info("--- Nuevo intento en /guardar_morbilidad ---")
    
    data = request.json
    tipo = data.get("tipo")
    clave = data.get("clave")
    enfermedades_seleccionadas = data.get("enfermedades", [])
    limitante = data.get("limitante", 0) 

    fecha_custom = data.get("fecha") 


    usuario = session.get('usuario', 'USUARIO_TEMP') 

    if not usuario or usuario == 'USUARIO_TEMP':
        logger.error("Error: Usuario no autenticado o temporal. Deteniendo el proceso.")
        pass 
    
    logger.debug("Usuario autenticado: %s", usuario)
    logger.debug("Datos recibidos: Tipo=%s, Clave=%s, Enfermedades=%s, Limitante=%s", tipo, clave, enfermedades_seleccionadas, limitante)

    if not tipo or not clave:
        logger.warning("Faltan datos (tipo/clave) en la petición.")
        return jsonify({"status": "error", "message": "Faltan datos (tipo/clave)"}), 400

    tipo_corto = None
    # ⭐ Mapeo de los tres tipos a su valor corto (P, H, C)
    if tipo == "preempleo":
        tipo_corto = "P"
    elif tipo == "historia":
        tipo_corto = "H"
    elif tipo == "consulta": 
        tipo_corto = "C"
    else:
        logger.error("Tipo de formulario '%s' inválido. Solo se acepta 'preempleo', 'historia' o 'consulta'.", tipo)
        return jsonify({"status": "error", "message": "Tipo de formulario inválido"}), 400

    conexion = None
    cursor = None
    id_hist_morb = None
    
    try:

        logger.info("Conectando a la base de datos de PRODUCCIÓN para la escritura...")
        conexion = obtener_conexion_produccion()
        cursor = conexion.cursor()
        logger.info("Conexión a la DB establecida.")

        # Paso 1: Buscar si ya existe un registro en EM_HISTORIA_MORBILIDAD
        logger.info("Buscando registro existente para Tipo='%s' y Número='%s'...", tipo_corto, clave)
        cursor.execute(
            """
            SELECT ID_HIST_MORB FROM EM_HISTORIA_MORBILIDAD
            WHERE TIPO = :p_tipo AND NUMERO = :p_numero
            """,
            {"p_tipo": tipo_corto, "p_numero": clave}
        )
        
        resultado_id = cursor.fetchone()
        
        if resultado_id:
            id_hist_morb = resultado_id[0]
            logger.info("Registro existente encontrado. ID_HIST_MORB: %s", id_hist_morb)
            
            # Paso 2.1: Actualizar LIMITANTE y USUARIO
            logger.info("Actualizando registro existente en EM_HISTORIA_MORBILIDAD. LIMITANTE: %s", limitante)
           
            if fecha_custom:
                cursor.execute("""
                    UPDATE EM_HISTORIA_MORBILIDAD 
                    SET LIMITANTE = :p_limitante, 
                        USUARIO = :p_usuario,
                        FECHA = TO_DATE(:p_fecha, 'YYYY-MM-DD')
                    WHERE ID_HIST_MORB = :p_id
                """, {
                    "p_limitante": limitante, 
                    "p_usuario": usuario, 
                    "p_fecha": fecha_custom,
                    "p_id": id_hist_morb
                })
            else:
                cursor.execute("""
                    UPDATE EM_HISTORIA_MORBILIDAD 
                    SET LIMITANTE = :p_limitante, 
                        USUARIO = :p_usuario
                    WHERE ID_HIST_MORB = :p_id
                """, {
                    "p_limitante": limitante, 
                    "p_usuario": usuario, 
                    "p_id": id_hist_morb
                })
            logger.info("LIMITANTE y USUARIO actualizados.")

        else:
            logger.info("No se encontró registro existente. Generando nuevo ID...")
           
            cursor.execute("SELECT PRODDTA.SEQ_EM_HISTORIA_MORBILIDAD.NEXTVAL FROM DUAL") 
            id_hist_morb = cursor.fetchone()[0]
            logger.info("Nuevo ID obtenido de la secuencia: %s", id_hist_morb)
            
            # Paso 2.2 (INSERT): Insertar nuevo registro, incluyendo LIMITANTE
            logger.info("Insertando nuevo registro en EM_HISTORIA_MORBILIDAD con LIMITANTE: %s", limitante)
            
            if fecha_custom:
              
                cursor.execute("""
                    INSERT INTO EM_HISTORIA_MORBILIDAD (ID_HIST_MORB, TIPO, NUMERO, USUARIO, LIMITANTE, FECHA)
                    VALUES (:p_id, :p_tipo, :p_numero, :p_usuario, :p_limitante, TO_DATE(:p_fecha, 'YYYY-MM-DD'))
                """, {
                    "p_id": id_hist_morb, 
                    "p_tipo": tipo_corto, 
                    "p_numero": clave, 
                    "p_usuario": usuario, 
                    "p_limitante": limitante,
                    "p_fecha": fecha_custom
                })
            else:
               
                cursor.execute("""
                    INSERT INTO EM_HISTORIA_MORBILIDAD (ID_HIST_MORB, TIPO, NUMERO, USUARIO, LIMITANTE, FECHA)
                    VALUES (:p_id, :p_tipo, :p_numero, :p_usuario, :p_limitante, SYSDATE)
                """, {
                    "p_id": id_hist_morb, 
                    "p_tipo": tipo_corto, 
                    "p_numero": clave, 
                    "p_usuario": usuario, 
                    "p_limitante": limitante
                })
            logger.info("Nuevo registro creado exitosamente con ID: %s", id_hist_morb)


        
        logger.info("Eliminando detalles de morbilidad anteriores para ID_HIST_MORB: %s...", id_hist_morb)
        cursor.execute(
            """
            DELETE FROM EM_HISTORIA_MORBILIDAD_DETALLE
            WHERE ID_HIST_MORB = :p_id_hist_morb
            """,
            {"p_id_hist_morb": id_hist_morb}
        )
        logger.info("Detalles de morbilidad anteriores eliminados.")

        
        if enfermedades_seleccionadas:
            logger.info("Insertando %d nuevos detalles de morbilidad...", len(enfermedades_seleccionadas))
            
            
            for codigo_enfermedad in enfermedades_seleccionadas:
                cursor.execute(
                    """
                    INSERT INTO EM_HISTORIA_MORBILIDAD_DETALLE (ID_HIST_MORB, CODIGO_ENF)
                    VALUES (:p_id, :p_codigo)
                    """,
                    {"p_id": id_hist_morb, "p_codigo": codigo_enfermedad}
                )
                logger.debug("Detalle insertado para Código: %s.", codigo_enfermedad)
                
            logger.info("Todos los nuevos detalles de morbilidad insertados.")
        else:
            logger.info("No hay enfermedades seleccionadas para insertar.")

       
        conexion.commit()
        logger.info("Transacción completada y cambios guardados en la base de datos.")
        logger.info("--- Proceso finalizado exitosamente ---")
        return jsonify({"status": "success", "message": "Datos guardados y actualizados correctamente."})

    except Exception as e:
        logger.exception("Error al guardar morbilidad. A continuación se muestra el rastreo completo del error:")
        print(f"🔴 Error al guardar morbilidad: {e}", file=sys.stderr)
        if conexion:
            conexion.rollback()
            logger.info("Se ha realizado un rollback debido a un error.")
        return jsonify({"status": "error", "message": "Error al guardar los datos.", "detail": str(e)}), 500
    finally:
        if cursor: 
            cursor.close()
            logger.info("Cursor cerrado.")
        if conexion: 
            conexion.close()
            logger.info("Conexión cerrada.")


@app.route('/consultar', methods=['POST'])
def consultar_registros():
    """
    Busca registros de morbilidad (P, H, C) por rango de fechas y aplica filtros opcionales.
    """
    data = request.json or {}
    fecha_desde = data.get('fecha-desde')
    fecha_hasta = data.get('fecha-hasta')
    
  
    cedula_filtro = data.get('cedula', '').strip().upper()
    contrato_filtro = str(data.get('contrato', '')).strip().upper()
    clave_filtro = str(data.get('clave', '')).strip().upper()

    if not fecha_desde or not fecha_hasta:
        print("🔴 Faltan las fechas obligatorias.")
        return jsonify({"error": "Las fechas 'desde' y 'hasta' son obligatorias"}), 400

    conexion_produccion = None
    cursor_produccion = None
    resultados = []

    try:
        conexion_produccion = obtener_conexion_produccion()
        cursor_produccion = conexion_produccion.cursor()
        
        # Paso 1: Obtener los registros de morbilidad filtrados por rango de fecha
        query_morbilidad = """
            SELECT TIPO, NUMERO, FECHA, LIMITANTE
            FROM EM_HISTORIA_MORBILIDAD
            WHERE FECHA >= TO_DATE(:fecha_desde, 'YYYY-MM-DD')
              AND FECHA < TO_DATE(:fecha_hasta, 'YYYY-MM-DD') + 1
        """
        cursor_produccion.execute(query_morbilidad, {
            "fecha_desde": fecha_desde,
            "fecha_hasta": fecha_hasta
        })
        morbilidad_records = cursor_produccion.fetchall()
        print(f"DEBUG: Se encontraron {len(morbilidad_records)} registros principales.")

        # Paso 2: Iterar sobre los resultados
        for tipo, numero, fecha, limitante_db_raw in morbilidad_records:
            numero_str = str(numero).strip() if numero is not None else ""

            if clave_filtro and clave_filtro != numero_str.upper():
                continue

            if tipo == 'P':
                tabla_principal, campo_clave = "EM_PREEMPLEOS", "NUMEROPREEMPLEO"
            elif tipo == 'H':
                tabla_principal, campo_clave = "EM_HISTORIAS", "NUMEROHISTORIA"
            elif tipo == 'C':
                tabla_principal, campo_clave = "EM_CONSULTAS", "NUMEROCONSULTA"
            else:
                print(f"DEBUG: Tipo desconocido: {tipo}. Saltando.")
                continue

            sql_query_detalle = f"""
                SELECT TRIM(CEDULA), TRIM(TO_CHAR(CONTRATO))
                FROM {tabla_principal}
                WHERE {campo_clave} = :numero_str
            """
            cursor_produccion.execute(sql_query_detalle, {"numero_str": numero_str})
            detalle_data = cursor_produccion.fetchone()
            
            if not detalle_data:
                print(f"DEBUG: 🔴 No se halló detalle para {tipo}/{numero_str} en {tabla_principal}.")
                continue

            cedula_raw, contrato_raw = detalle_data
            cedula_actual = cedula_raw.upper() if cedula_raw else ""
            contrato_actual = contrato_raw.upper() if contrato_raw else ""

            if cedula_filtro and cedula_actual != cedula_filtro:
                continue
            if contrato_filtro and contrato_actual != contrato_filtro:
                continue

            nombre_afiliado = ""
            apellido_afiliado = ""

            if cedula_actual:
                if tipo == 'P':
                    query_name = "SELECT TRIM(AFILIADO) FROM CT_AFILIADOSAUX WHERE TRIM(UPPER(CEDULA)) = :cedula"
                    cursor_produccion.execute(query_name, {"cedula": cedula_actual})
                    res_nombre = cursor_produccion.fetchone()
                    
                    if res_nombre and res_nombre[0]:
                        nombre_completo = res_nombre[0]
                        last_space = nombre_completo.rfind(' ')
                        if last_space != -1:
                            nombre_afiliado = nombre_completo[:last_space].strip()
                            apellido_afiliado = nombre_completo[last_space + 1:].strip()
                        else:
                            nombre_afiliado = nombre_completo

                elif tipo in ('H', 'C'):
                    query_name = "SELECT TRIM(BFNOMBR), TRIM(BFAPELL) FROM F58AF005 WHERE TRIM(UPPER(BFCIRIF)) = :cedula"
                    cursor_produccion.execute(query_name, {"cedula": cedula_actual})
                    res_nombre = cursor_produccion.fetchone()
                    
                    if res_nombre:
                        nombre_afiliado = res_nombre[0] or ""
                        apellido_afiliado = res_nombre[1] or ""

            limitante_val = int(limitante_db_raw) if limitante_db_raw is not None else 0
            
            resultados.append({
                "clave": numero_str,
                "tipo": tipo,
                "fecha": fecha.strftime('%d-%m-%Y') if fecha else "N/A",
                "cedula": cedula_actual,
                "nombre": nombre_afiliado,
                "apellido": apellido_afiliado,
                "contrato": contrato_actual,
                "limitante": limitante_val
            })

        print(f"✅ Se devolvieron {len(resultados)} registros procesados.")
        return jsonify(resultados)

    except Exception as e:
        print(f"🔴 Error en /consultar: {e}", file=sys.stderr)
        return jsonify({"error": "Error interno del servidor", "detail": str(e)}), 500
    finally:
        if cursor_produccion: cursor_produccion.close()
        if conexion_produccion: conexion_produccion.close()

            


@app.route('/generar_estadistica', methods=['POST'])
def generar_estadistica():
    """
    Genera un reporte estadístico de morbilidad por enfermedad.
    - Con filtro por Contrato: Excluye Preempleos (tipo 'P') de las estadísticas de morbilidad.
    - Sin filtro por Contrato: Incluye todas las atenciones (H, C, P) en el rango de fechas.
    """
    data = request.json or {}
    fecha_desde = data.get('fecha-desde')
    fecha_hasta = data.get('fecha-hasta')

    contrato_filtro_original = data.get('contrato', '').strip()
    contrato_filtro = contrato_filtro_original.upper() if contrato_filtro_original else None

    if not fecha_desde or not fecha_hasta:
        return jsonify({"error": "Las fechas 'desde' y 'hasta' son obligatorias"}), 400

    conexion = None
    cursor = None

    try:
        conexion = obtener_conexion_produccion()
        cursor = conexion.cursor()

        fecha_hasta_obj = datetime.strptime(fecha_hasta, '%Y-%m-%d')
        fecha_hasta_ajustada = fecha_hasta_obj + timedelta(days=1)
        fecha_hasta_str = fecha_hasta_ajustada.strftime('%Y-%m-%d')

        query_estadistica = """
                    SELECT
                        Categoria, Enfermedad, TotalInicial, TotalPreempleos, TotalPreVacacional, TotalPostVacacional, TotalSeguimiento, TotalEgreso, TotalOtros,
                        (TotalInicial + TotalPreempleos + TotalPreVacacional + TotalPostVacacional + TotalSeguimiento + TotalEgreso + TotalOtros) AS TotalGeneral
                    FROM (
                        SELECT 
                            cat.descripcion AS Categoria,
                            enf.descripcion AS Enfermedad,
                            SUM(CASE WHEN (detalle.TIPO_CONSULTA LIKE '%INICIAL%' OR detalle.TIPO_CONSULTA LIKE '%1RA%VEZ%') AND detalle.tipo IN ('H', 'C') THEN 1 ELSE 0 END) AS TotalInicial,
                            SUM(CASE WHEN detalle.tipo = 'P' THEN 1 ELSE 0 END) AS TotalPreempleos, 
                            SUM(CASE WHEN detalle.TIPO_CONSULTA LIKE '%PRE%VACACIONAL%' AND detalle.tipo IN ('H', 'C') THEN 1 ELSE 0 END) AS TotalPreVacacional,
                            SUM(CASE WHEN detalle.TIPO_CONSULTA LIKE '%POST%VACACIONAL%' AND detalle.tipo IN ('H', 'C') THEN 1 ELSE 0 END) AS TotalPostVacacional,
                            SUM(CASE WHEN detalle.TIPO_CONSULTA LIKE '%SEGUIMIENTO%' AND detalle.tipo IN ('H', 'C') THEN 1 ELSE 0 END) AS TotalSeguimiento,
                            SUM(CASE WHEN detalle.TIPO_CONSULTA LIKE '%EGRESO%' AND detalle.tipo IN ('H', 'C') THEN 1 ELSE 0 END) AS TotalEgreso,
                            SUM(CASE WHEN detalle.tipo IN ('H', 'C')
                                    AND detalle.TIPO_CONSULTA NOT LIKE '%INICIAL%'
                                    AND detalle.TIPO_CONSULTA NOT LIKE '%1RA%VEZ%'
                                    AND detalle.TIPO_CONSULTA NOT LIKE '%PRE%VACACIONAL%'
                                    AND detalle.TIPO_CONSULTA NOT LIKE '%POST%VACACIONAL%'
                                    AND detalle.TIPO_CONSULTA NOT LIKE '%SEGUIMIENTO%'
                                    AND detalle.TIPO_CONSULTA NOT LIKE '%EGRESO%'
                                    AND detalle.TIPO_CONSULTA IS NOT NULL THEN 1 ELSE 0 END) AS TotalOtros
                        FROM em_categorias cat
                        INNER JOIN em_enfermedades enf ON cat.codigo = enf.codigo_categoria
                        LEFT JOIN (
                            SELECT
                                det.codigo_enf, mor.tipo,
                                UPPER(TRIM(tc.descripcion)) AS TIPO_CONSULTA
                            FROM em_historia_morbilidad_detalle det
                            INNER JOIN em_historia_morbilidad mor ON det.id_hist_morb = mor.id_hist_morb
                            LEFT JOIN em_preempleos pre ON mor.numero = pre.numeropreempleo AND mor.tipo = 'P'
                            LEFT JOIN EM_HISTORIAS hist ON mor.numero = hist.NUMEROHISTORIA AND mor.tipo = 'H'
                            LEFT JOIN EM_CONSULTAS cons ON mor.numero = cons.NUMEROCONSULTA AND mor.tipo = 'C'
                            
                            -- 🔥 EL JOIN A CITAS EXACTAMENTE COMO LO EXPLICAS (Con TRIM)
                            LEFT JOIN CT_CITAS c ON TRIM(c.cedula) = TRIM(COALESCE(pre.cedula, hist.cedula, cons.cedula))
                                                AND TRUNC(c.fecha) = TRUNC(mor.fecha)
                                                AND c.id_status IN (SELECT id_status FROM CT_STATUS WHERE UPPER(TRIM(descripcion)) = 'ATENDIDO')
                            LEFT JOIN CT_TIPODECONSULTAS tc ON c.id_tipodeconsulta = tc.id_tipodeconsulta
                            
                            WHERE mor.fecha >= TO_DATE(:fecha_desde, 'YYYY-MM-DD')
                            AND mor.fecha < TO_DATE(:fecha_hasta_str, 'YYYY-MM-DD')
                            AND (
                                :contrato_filtro IS NULL 
                                OR (UPPER(TRIM(COALESCE(hist.contrato, cons.contrato))) = :contrato_filtro AND mor.tipo IN ('H', 'C'))
                            )
                        ) detalle ON enf.codigo = detalle.codigo_enf
                        GROUP BY cat.descripcion, enf.descripcion
                        HAVING (
                            SUM(CASE WHEN (detalle.TIPO_CONSULTA LIKE '%INICIAL%' OR detalle.TIPO_CONSULTA LIKE '%1RA%VEZ%') AND detalle.tipo IN ('H', 'C') THEN 1 ELSE 0 END) +
                            SUM(CASE WHEN detalle.tipo = 'P' THEN 1 ELSE 0 END) +
                            SUM(CASE WHEN detalle.tipo = 'P' THEN 1 ELSE 0 END) +
                            SUM(CASE WHEN detalle.TIPO_CONSULTA LIKE '%PRE%VACACIONAL%' AND detalle.tipo IN ('H', 'C') THEN 1 ELSE 0 END) +
                            SUM(CASE WHEN detalle.TIPO_CONSULTA LIKE '%POST%VACACIONAL%' AND detalle.tipo IN ('H', 'C') THEN 1 ELSE 0 END) +
                            SUM(CASE WHEN detalle.TIPO_CONSULTA LIKE '%SEGUIMIENTO%' AND detalle.tipo IN ('H', 'C') THEN 1 ELSE 0 END) +
                            SUM(CASE WHEN detalle.TIPO_CONSULTA LIKE '%EGRESO%' AND detalle.tipo IN ('H', 'C') THEN 1 ELSE 0 END) +
                            SUM(CASE WHEN detalle.tipo IN ('H', 'C')
                                    AND detalle.TIPO_CONSULTA NOT LIKE '%INICIAL%'
                                    AND detalle.TIPO_CONSULTA NOT LIKE '%1RA%VEZ%'
                                    AND detalle.TIPO_CONSULTA NOT LIKE '%PRE%VACACIONAL%'
                                    AND detalle.TIPO_CONSULTA NOT LIKE '%POST%VACACIONAL%'
                                    AND detalle.TIPO_CONSULTA NOT LIKE '%SEGUIMIENTO%'
                                    AND detalle.TIPO_CONSULTA NOT LIKE '%EGRESO%'
                                    AND detalle.TIPO_CONSULTA IS NOT NULL THEN 1 ELSE 0 END)
                        ) > 0
                    )
                    ORDER BY Categoria, Enfermedad
                """
        parametros = {
            "fecha_desde": fecha_desde,
            "fecha_hasta_str": fecha_hasta_str,
            "contrato_filtro": contrato_filtro
        }

        cursor.execute(query_estadistica, parametros)
        estadistica_records = cursor.fetchall()
        columnas = [col[0] for col in cursor.description]
        estadistica_final = [dict(zip(columnas, row)) for row in estadistica_records]

        # 2. Query Total de Consultas Generales 
        query_total_consultas = """
                    SELECT 
                        SUM(CASE WHEN (UPPER(TRIM(tc.DESCRIPCION)) LIKE '%INICIAL%' OR UPPER(TRIM(tc.DESCRIPCION)) LIKE '%1RA%VEZ%') THEN 1 ELSE 0 END) AS TOTALINICIAL,
                        SUM(CASE WHEN UPPER(TRIM(tc.DESCRIPCION)) LIKE '%PRE%VACACIONAL%' THEN 1 ELSE 0 END) AS TOTALPREVACACIONAL,
                        SUM(CASE WHEN UPPER(TRIM(tc.DESCRIPCION)) LIKE '%POST%VACACIONAL%' THEN 1 ELSE 0 END) AS TOTALPOSTVACACIONAL,
                        SUM(CASE WHEN UPPER(TRIM(tc.DESCRIPCION)) LIKE '%SEGUIMIENTO%' THEN 1 ELSE 0 END) AS TOTALSEGUIMIENTO,
                        SUM(CASE WHEN UPPER(TRIM(tc.DESCRIPCION)) LIKE '%EGRESO%' THEN 1 ELSE 0 END) AS TOTALEGRESO,
                        SUM(CASE WHEN UPPER(TRIM(tc.DESCRIPCION)) NOT LIKE '%INICIAL%' 
                                AND UPPER(TRIM(tc.DESCRIPCION)) NOT LIKE '%1RA%VEZ%' 
                                AND UPPER(TRIM(tc.DESCRIPCION)) NOT LIKE '%PRE%VACACIONAL%'
                                AND UPPER(TRIM(tc.DESCRIPCION)) NOT LIKE '%POST%VACACIONAL%'
                                AND UPPER(TRIM(tc.DESCRIPCION)) NOT LIKE '%SEGUIMIENTO%'
                                AND UPPER(TRIM(tc.DESCRIPCION)) NOT LIKE '%EGRESO%' 
                                AND tc.DESCRIPCION IS NOT NULL THEN 1 ELSE 0 END) AS TOTALOTROS,
                        COUNT(c.NRODECITA) AS TOTALCONSULTASGENERAL
                    FROM CT_CITAS c
                    INNER JOIN CT_TIPODECONSULTAS tc ON c.ID_TIPODECONSULTA = tc.ID_TIPODECONSULTA
                    INNER JOIN CT_STATUS st ON c.ID_STATUS = st.ID_STATUS
                    WHERE c.FECHA >= TO_DATE(:fecha_desde, 'YYYY-MM-DD')
                    AND c.FECHA < TO_DATE(:fecha_hasta_str, 'YYYY-MM-DD')
                    AND UPPER(TRIM(st.DESCRIPCION)) = 'ATENDIDO'
                    AND EXISTS (
                        SELECT 1 
                        FROM em_historia_morbilidad mor
                        LEFT JOIN EM_HISTORIAS hist ON mor.numero = hist.NUMEROHISTORIA AND mor.tipo = 'H'
                        LEFT JOIN EM_CONSULTAS cons ON mor.numero = cons.NUMEROCONSULTA AND mor.tipo = 'C'
                        WHERE TRUNC(mor.fecha) = TRUNC(c.fecha)
                            AND (TRIM(hist.cedula) = TRIM(c.cedula) OR TRIM(cons.cedula) = TRIM(c.cedula))
                            AND mor.tipo IN ('H', 'C')
                            AND (:contrato_filtro IS NULL OR UPPER(TRIM(COALESCE(hist.contrato, cons.contrato))) = :contrato_filtro)
                    )
                """
        universo = 0
        muestra = 0
        preempleos = 0

        query_muestra = """
            SELECT COUNT(*) 
            FROM EM_HISTORIA_MORBILIDAD mor
            LEFT JOIN EM_HISTORIAS hist ON mor.numero = hist.NUMEROHISTORIA AND mor.tipo = 'H'
            LEFT JOIN EM_CONSULTAS cons ON mor.numero = cons.NUMEROCONSULTA AND mor.tipo = 'C'
            WHERE mor.fecha >= TO_DATE(:fecha_desde, 'YYYY-MM-DD')
            AND mor.fecha < TO_DATE(:fecha_hasta_str, 'YYYY-MM-DD')
            AND mor.tipo IN ('H', 'C')
            AND (
                :contrato_filtro IS NULL 
                OR UPPER(COALESCE(hist.contrato, cons.contrato)) = :contrato_filtro
            )
        """
        cursor.execute(query_muestra, parametros)
        res_m = cursor.fetchone()
        muestra = res_m[0] if res_m else 0

        query_preempleos = """
            SELECT COUNT(*) 
            FROM EM_PREEMPLEOS pre
            WHERE pre.fecha >= TO_DATE(:fecha_desde, 'YYYY-MM-DD')
            AND pre.fecha < TO_DATE(:fecha_hasta_str, 'YYYY-MM-DD')
            AND (
                :contrato_filtro IS NULL
                OR UPPER(pre.CONTRATO) = :contrato_filtro
            )
        """
        cursor.execute(query_preempleos, parametros)
        res_p = cursor.fetchone()
        preempleos = res_p[0] if res_p else 0

        if contrato_filtro:
            query_universo = """
                SELECT COUNT(*)
                FROM F58AF005
                WHERE BFNUEX LIKE '%' || :contrato_filtro || '%'
            """
            cursor.execute(query_universo, {"contrato_filtro": contrato_filtro})
            res_uni = cursor.fetchone()
            universo = res_uni[0] if res_uni and res_uni[0] else 0

        # Totales por tipo de consulta
        cursor.execute(query_total_consultas, parametros)
        total_consultas_data = cursor.fetchone()
        total_consultas_cols = [col[0] for col in cursor.description]
        total_consultas_detalladas = dict(zip(total_consultas_cols, total_consultas_data)) if total_consultas_data else {}


        return jsonify({
            "reporte": estadistica_final,
            "total_consultas": total_consultas_detalladas,
            "universo": universo,
            "muestra": muestra,
            "preempleos": preempleos,
            "tiene_filtro_contrato": bool(contrato_filtro)
        })

    except Exception as e:
        print(f"🔴 Error al generar la estadística: {e}", file=sys.stderr)
        return jsonify({"error": "Error interno", "detail": str(e)}), 500
    finally:
        if cursor: cursor.close()
        if conexion: conexion.close()


@app.route('/enviar_reporte_morbilidad', methods=['POST'])
def enviar_reporte_morbilidad():
    try:
        datos_json_recibidos = request.get_json()
        if not datos_json_recibidos:
            return jsonify({"error": "No se recibieron datos JSON válidos"}), 400
        
        # 1. Extraer datos básicos
        destinatario_raw = datos_json_recibidos.get('destinatario', '')
        asunto = datos_json_recibidos.get('asunto')
        cuerpo = datos_json_recibidos.get('cuerpo')
        lista_adjuntos = datos_json_recibidos.get('adjuntos', [])

        if not destinatario_raw or not lista_adjuntos:
            return jsonify({"error": "Datos incompletos"}), 400


        destinatarios_lista = [email.strip() for email in destinatario_raw.split(',') if email.strip()]

        archivos_datos = []
        for adj in lista_adjuntos:
            base64_content = adj['content'].split(',').pop()
            archivos_datos.append({
                'nombre': adj.get('filename', 'archivo.dat'),
                'bytes': base64.b64decode(base64_content),
                'mime': adj.get('mimeType', 'application/octet-stream')
            })

        resultados = []
        url_destino = 'https://api.rescarven.com/enviar_correo'

        for email in destinatarios_lista:

            archivos_para_enviar = [
                ('archivos', (a['nombre'], io.BytesIO(a['bytes']), a['mime'])) 
                for a in archivos_datos
            ]

            datos_finales = {
                "destinatarios": [email], 
                "asunto": asunto,
                "mensaje": {"tipo": "texto", "contenido": cuerpo}
            }

            try:
                response = requests.post(
                    url_destino, 
                    data={'datos': json.dumps(datos_finales)}, 
                    files=archivos_para_enviar
                )
                resultados.append({"email": email, "status": response.status_code})
            except Exception as e:
                resultados.append({"email": email, "error": str(e)})


        return jsonify({
            "message": "Proceso de envío finalizado",
            "detalles": resultados
        }), 200

    except Exception as e:
        print(f"Error Crítico: {e}")
        return jsonify({"error": str(e)}), 500


            
if __name__ == '__main__':
    host = "0.0.0.0" 
    port = 5002
    app.run(host=host, port=port, debug=True)
    
    
    
