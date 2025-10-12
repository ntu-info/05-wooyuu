# app.py
from flask import Flask, jsonify, abort, send_file
import os
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import OperationalError

_engine = None

def get_engine():
    global _engine
    if _engine is not None:
        return _engine
    db_url = os.getenv("DB_URL")
    if not db_url:
        raise RuntimeError("Missing DB_URL (or DATABASE_URL) environment variable.")
    # Normalize old 'postgres://' scheme to 'postgresql://'
    if db_url.startswith("postgres://"):
        db_url = "postgresql://" + db_url[len("postgres://"):]
    _engine = create_engine(
        db_url,
        pool_pre_ping=True,
    )
    return _engine

def create_app():
    app = Flask(__name__)

    @app.get("/", endpoint="health")
    def health():
        return "<p>Server working!</p>"

    @app.get("/img", endpoint="show_img")
    def show_img():
        return send_file("amygdala.gif", mimetype="image/gif")
    
    @app.get("/terms/<term>/studies", endpoint="terms_studies")
    def get_studies_by_term(term):
        return term

    @app.get("/locations/<coords>/studies", endpoint="locations_studies")
    def get_studies_by_coordinates(coords):
        x, y, z = map(int, coords.split("_"))
        return jsonify([x, y, z])

# ...existing code...
    @app.get("/debug/terms", endpoint="debug_terms")
    def debug_terms():
        """查看資料庫中所有不同的 term（限制 100 個）"""
        eng = get_engine()
        
        try:
            with eng.begin() as conn:
                conn.execute(text("SET search_path TO ns, public;"))
                
                # 取得所有不同的 term
                result = conn.execute(text("""
                    SELECT DISTINCT term 
                    FROM ns.annotations_terms 
                    ORDER BY term 
                    LIMIT 100
                """)).fetchall()
                
                terms = [row[0] for row in result]
                
                return jsonify({
                    "count": len(terms),
                    "terms": terms
                }), 200
                
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    
    @app.get("/debug/search_term/<keyword>", endpoint="debug_search_term")
    def debug_search_term(keyword):
        """搜尋包含某關鍵字的 term"""
        eng = get_engine()
        
        try:
            with eng.begin() as conn:
                conn.execute(text("SET search_path TO ns, public;"))
                
                # 使用 LIKE 模糊搜尋
                result = conn.execute(text("""
                    SELECT DISTINCT term, COUNT(*) as count
                    FROM ns.annotations_terms
                    WHERE term LIKE :keyword
                    GROUP BY term
                    ORDER BY count DESC
                    LIMIT 20
                """), {"keyword": f"%{keyword}%"}).fetchall()
                
                terms = [{"term": row[0], "count": row[1]} for row in result]
                
                return jsonify({
                    "keyword": keyword,
                    "matches": len(terms),
                    "terms": terms
                }), 200
                
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.get("/dissociate/terms/<term_a>/<term_b>", endpoint="dissociate_terms")
    def dissociate_terms(term_a, term_b):
        """
        回傳提到 term_a 但沒提到 term_b 的研究
        支援模糊比對（LIKE）
        """
        eng = get_engine()
        
        try:
            with eng.begin() as conn:
                conn.execute(text("SET search_path TO ns, public;"))
                
                # 方法 1：精確比對
                query_exact = text("""
                    SELECT DISTINCT study_id
                    FROM ns.annotations_terms
                    WHERE term = :term_a
                    
                    EXCEPT
                    
                    SELECT DISTINCT study_id
                    FROM ns.annotations_terms
                    WHERE term = :term_b
                    
                    ORDER BY study_id
                """)
                
                result_exact = conn.execute(query_exact, {
                    "term_a": term_a,
                    "term_b": term_b
                }).fetchall()
                
                # 如果精確比對沒結果，試試模糊比對
                if not result_exact:
                    query_like = text("""
                        SELECT DISTINCT study_id
                        FROM ns.annotations_terms
                        WHERE term LIKE :term_a
                        
                        EXCEPT
                        
                        SELECT DISTINCT study_id
                        FROM ns.annotations_terms
                        WHERE term LIKE :term_b
                        
                        ORDER BY study_id
                    """)
                    
                    result_like = conn.execute(query_like, {
                        "term_a": f"%{term_a}%",
                        "term_b": f"%{term_b}%"
                    }).fetchall()
                    
                    studies = [row[0] for row in result_like]
                    match_type = "fuzzy (LIKE)"
                else:
                    studies = [row[0] for row in result_exact]
                    match_type = "exact"
                
                return jsonify({
                    "term_a": term_a,
                    "term_b": term_b,
                    "match_type": match_type,
                    "dissociation": "A \\ B (studies with A but not B)",
                    "count": len(studies),
                    "studies": studies[:50]  # 限制回傳數量避免太大
                }), 200
                
        except Exception as e:
            return jsonify({
                "error": str(e),
                "term_a": term_a,
                "term_b": term_b
            }), 500

    @app.get("/dissociate/locations/<coords_a>/<coords_b>", endpoint="dissociate_locations")
    def dissociate_locations(coords_a, coords_b):
        """
        回傳提到座標 A 但沒提到座標 B 的研究
        座標格式：x_y_z（例如：0_-52_26）
        """
        try:
            # 解析座標
            x1, y1, z1 = map(float, coords_a.split("_"))
            x2, y2, z2 = map(float, coords_b.split("_"))
        except ValueError:
            return jsonify({
                "error": "Invalid coordinate format. Use x_y_z (e.g., 0_-52_26)"
            }), 400
        
        eng = get_engine()
        
        try:
            with eng.begin() as conn:
                conn.execute(text("SET search_path TO ns, public;"))
                
                # 使用 PostGIS 的空間查詢
                query = text("""
                    SELECT DISTINCT study_id
                    FROM ns.coordinates
                    WHERE ST_3DDistance(
                        geom,
                        ST_SetSRID(ST_MakePoint(:x1, :y1, :z1), 4326)
                    ) < 5  -- 距離 A < 5mm
                    
                    EXCEPT
                    
                    SELECT DISTINCT study_id
                    FROM ns.coordinates
                    WHERE ST_3DDistance(
                        geom,
                        ST_SetSRID(ST_MakePoint(:x2, :y2, :z2), 4326)
                    ) < 5  -- 排除距離 B < 5mm 的
                    
                    ORDER BY study_id
                """)
                
                result = conn.execute(query, {
                    "x1": x1, "y1": y1, "z1": z1,
                    "x2": x2, "y2": y2, "z2": z2
                }).fetchall()
                
                studies = [row[0] for row in result]
                
                return jsonify({
                    "location_a": {"x": x1, "y": y1, "z": z1},
                    "location_b": {"x": x2, "y": y2, "z": z2},
                    "dissociation": "A \\ B (studies near A but not near B)",
                    "distance_threshold_mm": 5,
                    "count": len(studies),
                    "studies": studies
                }), 200
                
        except Exception as e:
            return jsonify({
                "error": str(e),
                "location_a": coords_a,
                "location_b": coords_b
            }), 500

# ...existing code...

    @app.get("/test_db", endpoint="test_db")
    def test_db():
        eng = get_engine()
        payload = {"ok": False, "dialect": eng.dialect.name}

        try:
            with eng.begin() as conn:
                # Ensure we are in the correct schema
                conn.execute(text("SET search_path TO ns, public;"))
                payload["version"] = conn.exec_driver_sql("SELECT version()").scalar()

                # Counts
                payload["coordinates_count"] = conn.execute(text("SELECT COUNT(*) FROM ns.coordinates")).scalar()
                payload["metadata_count"] = conn.execute(text("SELECT COUNT(*) FROM ns.metadata")).scalar()
                payload["annotations_terms_count"] = conn.execute(text("SELECT COUNT(*) FROM ns.annotations_terms")).scalar()

                # Samples
                try:
                    rows = conn.execute(text(
                        "SELECT study_id, ST_X(geom) AS x, ST_Y(geom) AS y, ST_Z(geom) AS z FROM ns.coordinates LIMIT 3"
                    )).mappings().all()
                    payload["coordinates_sample"] = [dict(r) for r in rows]
                except Exception:
                    payload["coordinates_sample"] = []

                try:
                    # Select a few columns if they exist; otherwise select a generic subset
                    rows = conn.execute(text("SELECT * FROM ns.metadata LIMIT 3")).mappings().all()
                    payload["metadata_sample"] = [dict(r) for r in rows]
                except Exception:
                    payload["metadata_sample"] = []

                try:
                    rows = conn.execute(text(
                        "SELECT study_id, contrast_id, term, weight FROM ns.annotations_terms LIMIT 3"
                    )).mappings().all()
                    payload["annotations_terms_sample"] = [dict(r) for r in rows]
                except Exception:
                    payload["annotations_terms_sample"] = []

            payload["ok"] = True
            return jsonify(payload), 200

        except Exception as e:
            payload["error"] = str(e)
            return jsonify(payload), 500

    return app

# WSGI entry point (no __main__)
app = create_app()
