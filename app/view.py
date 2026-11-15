from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

@router.get("/job/{job_id}", response_class=HTMLResponse)
async def get_cleaning_job_page(job_id: str):
    
    property_name = "Kolonaki Loft (Αθήνα)"
    date = "21/06/2026"
    checkout_time = "11:00 π.μ."
    
    html_content = f"""
    <!DOCTYPE html>
    <html lang="el">
    <head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Πρόγραμμα Καθαρισμού - {property_name}</title>
    <style>
      body {{
        font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
        background-color: #f4f7f6;
        display: flex;
        justify-content: center;
        padding: 20px;
        margin: 0;
      }}

      .card {{
        background: #ffffff;
        border-radius: 12px;
        box-shadow: 0 4px 15px rgba(0,0,0,0.08);
        max-width: 500px;
        width: 100%;
        overflow: hidden;
        border: 1px solid #e1e8ed;
      }}

      .header {{
        background: #007bff;
        color: white;
        padding: 20px;
        text-align: center;
      }}

      .header h2 {{
        margin: 0;
        font-size: 1.3rem;
      }}

      .header p {{
        margin: 5px 0 0;
        font-size: 0.9rem;
        opacity: 0.9;
      }}

      .info-grid {{
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 1px;
        background-color: #e1e8ed;
        border-bottom: 1px solid #e1e8ed;
      }}

      .info-box {{
        background-color: #ffffff;
        padding: 15px;
        text-align: center;
      }}

      .info-label {{
        font-size: 0.75rem;
        color: #657786;
        text-transform: uppercase;
        letter-spacing: 0.5px;
        margin-bottom: 4px;
      }}

      .info-value {{
        font-size: 1.05rem;
        font-weight: bold;
        color: #14171a;
      }}

      .tasks-section {{
        padding: 20px;
      }}

      .tasks-title {{
        font-size: 1rem;
        font-weight: bold;
        color: #14171a;
        margin-bottom: 12px;
      }}

      .tasks-grid {{
        display: grid;
        grid-template-columns: 1fr;
        gap: 10px;
      }}

      .task-item {{
        display: grid;
        grid-template-columns: 30px 1fr auto;
        align-items: center;
        background: #f8fafc;
        border: 1px solid #e2e8f0;
        border-radius: 8px;
        padding: 12px;
      }}

      .task-icon {{
        font-size: 1.2rem;
      }}

      .task-name {{
        font-size: 0.95rem;
        color: #334155;
        font-weight: 500;
      }}

      .task-badge {{
        background: #e0f2fe;
        color: #0369a1;
        font-size: 0.75rem;
        padding: 4px 8px;
        border-radius: 12px;
        font-weight: bold;
      }}

      .actions-grid {{
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 12px;
        padding: 0 20px 20px;
      }}

      .btn {{
        padding: 12px;
        border: none;
        border-radius: 8px;
        font-size: 1rem;
        font-weight: bold;
        cursor: pointer;
        text-align: center;
        color: white;
      }}

      .btn-accept {{
        background-color: #10b981;
      }}

      .btn-decline {{
        background-color: #ef4444;
      }}
    </style>
    </head>
    <body>

    <div class="card">
      <div class="header">
        <h2>🧹 Νέα Ανάθεση Καθαρισμού</h2>
        <p>Γεια σου Μαρία! Έχεις νέο πρόγραμμα.</p>
      </div>

      <div class="info-grid">
        <div class="info-box">
          <div class="info-label">Ακίνητο</div>
          <div class="info-value">{property_name}</div>
        </div>
        <div class="info-box">
          <div class="info-label">ID Δουλειάς</div>
          <div class="info-value">#{job_id}</div>
        </div>
        <div class="info-box">
          <div class="info-label">Ημερομηνία</div>
          <div class="info-value">{date}</div>
        </div>
        <div class="info-box">
          <div class="info-label">Ώρα Check-out</div>
          <div class="info-value">{checkout_time}</div>
        </div>
      </div>

      <div class="tasks-section">
        <div class="tasks-title">📋 Λίστα Εργασιών</div>
        <div class="tasks-grid">
          <div class="task-item">
            <span class="task-icon">🛏️</span>
            <span class="task-name">Αλλαγή σεντονιών & πετσετών</span>
            <span class="task-badge">Υποχρεωτικό</span>
          </div>
          <div class="task-item">
            <span class="task-icon">🚿</span>
            <span class="task-name">Απολύμανση μπάνιου & κουζίνας</span>
            <span class="task-badge">Υποχρεωτικό</span>
          </div>
          <div class="task-item">
            <span class="task-icon">🧼</span>
            <span class="task-name">Σκούπισμα & σφουγγάρισμα χώρων</span>
            <span class="task-badge">Υποχρεωτικό</span>
          </div>
          <div class="task-item">
            <span class="task-icon">🧴</span>
            <span class="task-name">Αναπλήρωση αναλωσίμων</span>
            <span class="task-badge">Έλεγχος</span>
          </div>
        </div>
      </div>

      <div class="actions-grid">
        <button class="btn btn-accept" onclick="alert('Αποδοχή δουλειάς!')">✅ Αποδοχή</button>
        <button class="btn btn-decline" onclick="alert('Απόρριψη δουλειάς!')">❌ Απόρριψη</button>
      </div>
    </div>

    </body>
    </html>
    """
    return HTMLResponse(content=html_content)