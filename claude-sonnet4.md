# Neurosynth Backend Development Q&A Summary

## Q1: What is the assignment about?

**A:** Build a Neurosynth Backend service that provides **functional dissociation** queries.

### Core Features
1. **Dissociate by Terms** (`/dissociate/terms/<term_a>/<term_b>`)
   - Returns studies that mention `term_a` but NOT `term_b`

2. **Dissociate by MNI Coordinates** (`/dissociate/locations/<x1_y1_z1>/<x2_y2_z2>`)
   - Returns studies near coordinate A but NOT near coordinate B
   - Coordinate format: `x_y_z` (e.g., `0_-52_26`)

---

## Q2: What do PostgreSQL, Render, Flask, and Gunicorn mean?

**A:** 

| Technology | Role | Analogy |
|------------|------|---------|
| **PostgreSQL** | Relational Database | Filing Cabinet - stores and queries data |
| **Render** | Cloud Hosting Platform | Cloud Landlord - provides 24/7 hosting |
| **Flask** | Python Web Framework | Restaurant Menu System - creates API endpoints |
| **Gunicorn** | Production Server | Restaurant Manager - handles multiple requests efficiently |

### Collaboration Flow
```
User Browser
    ↓ (sends request)
[Render Cloud Platform]
    ↓
[Gunicorn Server]
    ↓
[Flask Application]
    ↓
[PostgreSQL Database]
    ↓
Returns result to user ✅
```

---

## Q3: Why is MySQL NOT recommended?

**A:** PostgreSQL is superior to MySQL for this assignment:

### Main Reasons
1. **Native ARRAY Support**
   ```sql
   -- PostgreSQL ✅
   keywords TEXT[]  -- native array support
   
   -- MySQL ❌
   keywords VARCHAR(255)  -- string only
   ```

2. **PostGIS Spatial Extension**
   - PostgreSQL has PostGIS for handling MNI coordinates
   - Supports 3D distance calculations

3. **Advanced Query Capabilities**
   - Supports Window Functions, CTEs
   - More powerful full-text search

---

## Q4: What does check_db.py do?

**A:** Verifies that the PostgreSQL database is properly configured.

### Checks Performed
1. **Environment**: Database version, connection status
2. **tsvector**: Full-text search functionality
3. **pgvector**: Vector similarity calculations
4. **PostGIS**: Spatial coordinate operations

### Execution Result
```bash
python3 check_db.py --url "postgresql://user:pass@host/db"

=== Environment / sanity checks ===
✅ server_version: PostgreSQL 17.6
✅ current database: neurosynth_backend_api

=== Check PostGIS (Spatial) ===
✅ PostGIS extension installed: 3.5.3
```

---

## Q5: What is the Render deployment process?

**A:** 

### Steps
1. **Fork example project** (or use your own repo)
2. **Connect Render to GitHub**
3. **Configure deployment parameters**:
   - Language: `Python 3`
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `gunicorn app:app`

### Automated Deployment
```
GitHub push → Render auto-detects → Builds → Deploys → Live 🎉
```

---

## Q6: What is the DB_URL environment variable and why is it needed?

**A:** Environment variables are like "secret configuration files" for your app.

### Why is it needed?
```python
# app.py
db_url = os.getenv("DB_URL")  # Read from environment variable
if not db_url:
    raise RuntimeError("Missing DB_URL environment variable.")
```

### DB_URL Structure
```
postgresql://username:password@host:port/database_name
```

### Setting in Render
```
Environment → Add Environment Variable
Key: DB_URL
Value: postgresql://user:pass@host:5432/dbname
```

**Without it**: All database-dependent endpoints will fail ❌

---

## Q7: What is "Clear build cache & deploy"?

**A:** 

### Normal Deploy vs Clear Cache Deploy

| Aspect | Normal Deploy | Clear Cache Deploy |
|--------|--------------|-------------------|
| Speed | ⚡ 30s-1min | 🐢 3-5 minutes |
| Use Case | Regular code updates | Troubleshooting issues |
| Cache Status | Keeps old cache | Clears everything |

### When to use Clear Cache?
- Updating Python version
- Package installation failures
- Mysterious build errors
- Dependency version conflicts

**Note**: Clearing cache does **NOT** affect database content or environment variables.

---

## Q8: Implementing Terms Dissociation API

**A:** 

### Core Logic
Use SQL's `EXCEPT` operator (set difference):

```python
@app.get("/dissociate/terms/<term_a>/<term_b>")
def dissociate_terms(term_a, term_b):
    eng = get_engine()
    
    with eng.begin() as conn:
        conn.execute(text("SET search_path TO ns, public;"))
        
        # Support multiple term formats
        term_formats = [
            (term_a, term_b, "exact"),
            (f"terms_abstract_tfidf__{term_a}", 
             f"terms_abstract_tfidf__{term_b}", "tfidf_exact"),
            (f"%{term_a}%", f"%{term_b}%", "fuzzy")
        ]
        
        for fmt_a, fmt_b, fmt_name in term_formats:
            if fmt_name == "fuzzy":
                query = text("""
                    SELECT DISTINCT study_id
                    FROM ns.annotations_terms
                    WHERE term LIKE :term_a
                    EXCEPT
                    SELECT DISTINCT study_id
                    FROM ns.annotations_terms
                    WHERE term LIKE :term_b
                    ORDER BY study_id
                """)
            else:
                query = text("""
                    SELECT DISTINCT study_id
                    FROM ns.annotations_terms
                    WHERE term = :term_a
                    EXCEPT
                    SELECT DISTINCT study_id
                    FROM ns.annotations_terms
                    WHERE term = :term_b
                    ORDER BY study_id
                """)
            
            result = conn.execute(query, {
                "term_a": fmt_a, "term_b": fmt_b
            }).fetchall()
            
            if result:
                studies = [row[0] for row in result]
                return jsonify({
                    "term_a": term_a,
                    "term_b": term_b,
                    "match_type": fmt_name,
                    "count": len(studies),
                    "studies": studies[:100]
                }), 200
        
        return jsonify({
            "term_a": term_a,
            "term_b": term_b,
            "match_type": "no_match",
            "count": 0,
            "studies": []
        }), 200
```

### Testing
```bash
curl https://ns-backend-u485.onrender.com/dissociate/terms/posterior_cingulate/ventromedial_prefrontal
# ✅ {"count": 897, "match_type": "fuzzy", ...}
```

---

## Q9: Implementing Locations Dissociation API

**A:** 

### Core Logic
Use PostGIS for 3D distance calculations:

```python
@app.get("/dissociate/locations/<coords_a>/<coords_b>")
def dissociate_locations(coords_a, coords_b):
    try:
        x1, y1, z1 = map(float, coords_a.split("_"))
        x2, y2, z2 = map(float, coords_b.split("_"))
    except ValueError:
        return jsonify({
            "error": "Invalid coordinate format. Use x_y_z"
        }), 400
    
    eng = get_engine()
    
    with eng.begin() as conn:
        conn.execute(text("SET search_path TO ns, public;"))
        
        query = text("""
            SELECT DISTINCT study_id
            FROM ns.coordinates
            WHERE ST_3DDistance(
                geom,
                ST_SetSRID(ST_MakePoint(:x1, :y1, :z1), 4326)
            ) < 5  -- Distance to A < 5mm
            
            EXCEPT
            
            SELECT DISTINCT study_id
            FROM ns.coordinates
            WHERE ST_3DDistance(
                geom,
                ST_SetSRID(ST_MakePoint(:x2, :y2, :z2), 4326)
            ) < 5  -- Exclude studies near B (< 5mm)
            
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
```

### Testing
```bash
curl https://ns-backend-u485.onrender.com/dissociate/locations/0_-52_26/-2_50_-6
# ✅ {"count": 284, ...}
```

---

## Q10: Debugging Empty Results from Terms API

**A:** 

### Problem Analysis
Database terms have format `terms_abstract_tfidf__keyword`, not just plain keywords.

### Solution
Add debug endpoints to inspect actual term formats:

```python
@app.get("/debug/terms")
def debug_terms():
    eng = get_engine()
    with eng.begin() as conn:
        conn.execute(text("SET search_path TO ns, public;"))
        result = conn.execute(text("""
            SELECT DISTINCT term 
            FROM ns.annotations_terms 
            ORDER BY term 
            LIMIT 100
        """)).fetchall()
        
        terms = [row[0] for row in result]
        return jsonify({"count": len(terms), "terms": terms}), 200

@app.get("/debug/search_term/<keyword>")
def debug_search_term(keyword):
    eng = get_engine()
    with eng.begin() as conn:
        conn.execute(text("SET search_path TO ns, public;"))
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
```

### Final Solution
Added automatic format detection in `dissociate_terms` function, supporting:
1. Exact match (`posterior_cingulate`)
2. Prefix match (`terms_abstract_tfidf__posterior_cingulate`)
3. Fuzzy match (`%posterior_cingulate%`)

---

## Quick Command Reference

```bash
# Check database
python3 check_db.py --url "postgresql://..."

# Create database
python3 create_db.py --url "postgresql://..."

# Local testing
flask run

# Deploy to Render
git add .
git commit -m "Update"
git push origin main

# Test API
curl https://your-app.onrender.com/test_db
```

---