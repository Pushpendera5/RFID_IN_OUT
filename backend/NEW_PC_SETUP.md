# New PC Quick Setup

## 1) One-time setup
```powershell
cd C:\Users\<USER>\Desktop\Kol_jewellery\backend
.\setup_new_pc.ps1 -DbServer "127.0.0.1" -DbPort 1433 -DbName "jewellery_db" -DbUser "sa" -DbPassword "1234"
```

## 2) Run application
```powershell
cd C:\Users\<USER>\Desktop\Kol_jewellery\backend
.\run_app.ps1
```

## 3) Optional: auto-start and auto-restart on reboot/crash
Run PowerShell as Administrator:
```powershell
cd C:\Users\<USER>\Desktop\Kol_jewellery\backend
.\install_autorun_task.ps1
```

To remove this task later:
```powershell
.\remove_autorun_task.ps1
```

## 4) Open in browser
```text
http://<PC-IP>:8000/login
```
