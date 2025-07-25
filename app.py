from flask import Flask, request, jsonify, render_template, redirect, url_for, session
from flask_cors import CORS
import cv2
import numpy as np
import easyocr
import base64
import subprocess
import json
import csv
import os
import ast
from datetime import datetime
from save_data import save_vehicle_log

app = Flask(__name__, static_url_path='/static', static_folder='static', template_folder='template')

print("Template folder path:", os.path.abspath('template'))
print("Files inside template:", os.listdir('template'))
app.secret_key = 'your_secret_key_here'  

plate_cascade = cv2.CascadeClassifier('haarcascade_russian_plate_number.xml')
reader = easyocr.Reader(['en'])

CORS(app)

@app.after_request
def add_no_cache_headers(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, post-check=0, pre-check=0, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

# -------- DEFAULT REDIRECT TO ADMIN LOGIN --------
@app.route('/')
def index():
    return redirect(url_for('admin_login'))

# -------- ADMIN LOGIN --------
@app.route('/adminlogin', methods=['GET', 'POST'])
def admin_login():
    if 'admin' in session:
        return redirect(url_for('admin_landing'))
    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        if username == 'admin' and password == 'adminpass':
            session['admin'] = username
            return redirect(url_for('admin_landing'))
        else:
            error = "Invalid admin credentials"
    return render_template('adminlogin.html', error=error)

# -------- ADMIN LANDING PAGE --------
@app.route('/admin/landing')
def admin_landing():
    if 'admin' not in session:
        return redirect(url_for('admin_login'))
    return render_template('index.html')

#---------start Camera----
@app.route('/camera')
def camera_page():
    if 'admin' not in session:
        return redirect(url_for('admin_login'))
    return render_template('camera.html')

# # -------- ADMIN DASHBOARD --------
# @app.route('/admin/dashboard')
# def admin_dashboard():
#     if 'admin' not in session:
#         return redirect(url_for('admin_login'))
#     return render_template('admin_dashboard.html')

# #----------slot view------
# @app.route('/slotview')
# def slotview():
#     return render_template('slotview.html')

#-------layout--------------
@app.route('/layoutview')
def layout_view():
    return render_template('layoutview.html')


# #----------car records-------
# @app.route('/carrecords')
# def carrecords():
#     return render_template('carrecords.html')

# -------- ADMIN LOGOUT --------
@app.route('/adminlogout')
def admin_logout():
    session.pop('admin', None)
    return redirect(url_for('admin_login'))

# -------- USER DASHBOARD --------
@app.route('/userdashboard')
def user_dashboard():
    plate = request.args.get('plate', 'Unknown')
    slot = request.args.get('slot', 'N/A')
    time = request.args.get('time', 'N/A')
    bill = 50  
    raw_path = request.args.get('path', '')
    path = raw_path.split(',') if raw_path else [f'Main Gate', f'Slot {slot}']

    return render_template('user_dashboard.html', plate=plate, entry_time=time, slot=slot, path=path, bill=bill)

# function to save cropped plate image
def save_plate_image(image, plate_text):
    folder_path = 'plates/plate_img'
    os.makedirs(folder_path, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{plate_text}_{timestamp}.jpg"
    filepath = os.path.join(folder_path, filename)
    cv2.imwrite(filepath, image)
    return filepath

# function to log data into CSV
def log_vehicle(plate_text, slot):
    log_file = 'vehicle_logs.csv'
    file_exists = os.path.isfile(log_file)
    with open(log_file, 'a', newline='') as csvfile:
        fieldnames = ['Plate', 'slot', 'Timestamp']
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        writer.writerow({
            'Plate': plate_text.strip().replace(" ", ""),
            'slot': slot if slot else 'N/A',
            'Timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })

# -------- OCR IMAGE UPLOAD --------
@app.route('/upload', methods=['POST'])
def upload_image():
    try:
        if not request.is_json:
            return jsonify({'error': 'Content-Type must be application/json'}), 400

        img_data = request.json.get('image', '')
        if not img_data or ',' not in img_data:
            return jsonify({'error': 'Invalid image data'}), 400

        img_str = img_data.split(',')[1]
        img_bytes = base64.b64decode(img_str)
        img_array = np.frombuffer(img_bytes, np.uint8)
        img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
        if img is None:
            return jsonify({'error': 'Failed to decode image'}), 400

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        plates = plate_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5)
        print("Plates detected:", plates)

        plate_text = "Not Detected"
        cropped_plate_img = None

        for (x, y, w, h) in plates:
            cropped_plate_img = img[y:y+h, x:x+w]
            plate_gray = cv2.cvtColor(cropped_plate_img, cv2.COLOR_BGR2GRAY)
            plate_resized = cv2.resize(plate_gray, (1024, 256))
            plate_text = read_plate_text(plate_resized)
            print("OCR Plate Text:", plate_text)
            break

        if plate_text == "Not Detected" or cropped_plate_img is None:
            return jsonify({"error": "Plate number not detected. Please retake the photo."}), 400

        plate_text = plate_text.strip().replace(" ", "")
        save_plate_image(cropped_plate_img, plate_text)

        try:
            result = subprocess.run(['./parking', plate_text], capture_output=True, text=True, timeout=10)
            if result.returncode != 0:
                return jsonify({'plate_number': plate_text, 'error': 'Parking allocation failed'}), 500

            output_json = result.stdout.strip()
            try:
                output = json.loads(output_json)
            except Exception:
                output = {}
                lines = result.stdout.strip().split('\n')
                for line in lines:
                    if "slot" in line.lower():
                        output['slot'] = line.split(':')[-1].strip()
                    if "path" in line.lower():
                        output['path'] = line.split(':')[-1].strip().split()

            slot = output.get('slot', 'N/A')
            path = output.get('path', [])
            entry_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            log_vehicle(plate_text, slot)
            return jsonify({
                'plate_number': plate_text,
                'slot': slot,
                'time': entry_time,
                'path': path
            })

        except Exception as e:
            return jsonify({
                "plate_number": plate_text,
                "error": f"Allocation backend failed: {str(e)}"
            }), 500

    except Exception as e:
        print("Error during upload:", str(e))
        return jsonify({"error": "Failed to process image"}), 500

#--------- show_path -----
@app.route('/show_path_graph', methods=['POST'])
def show_path_graph():
    raw_path = request.form.get('path', '')
    try:
        path = ast.literal_eval(raw_path)         
        return render_template('path_graph.html', path=path)
    except Exception as e:
        return jsonify({'error': f'Failed to parse path: {str(e)}'}), 400



# -------- OCR FUNCTION --------
def read_plate_text(image):
    results = sorted(reader.readtext(image), key=lambda x: x[2], reverse=True)
    for (bbox, text, prob) in results:
        if len(text) > 3:
            return text.strip()
    return "Not Detected"

# -------- SLOT ALLOCATION --------
@app.route('/allocate', methods=['POST'])
def allocate_slot():
    if not request.is_json:
        return jsonify({'error': 'Content-Type must be application/json'}), 400

    data = request.get_json()
    plate_number = data.get('plateNumber')

    if not plate_number:
        return jsonify({'error': 'No plate number provided'}), 400

    try:
        result = subprocess.run(['./parking', plate_number], capture_output=True, text=True, timeout=10)
        print("Subprocess output:\n", result.stdout)

        if result.returncode != 0:
            return jsonify({
                "plate_number": plate_number,
                "error": "No available slot or error occurred",
                "raw_output": result.stdout
            }), 400
        try:
            cxx_output = json.loads(result.stdout)
        except json.JSONDecodeError:
            return jsonify({'error': 'C++ output not in JSON format', 'raw_output': result.stdout}), 500

        slot = cxx_output.get("slot")
        path = cxx_output.get("path")
        if not slot:
            return jsonify({'error': 'Slot not found in C++ output', 'raw_output': result.stdout}), 500
        print(slot)
        return jsonify({
            "plate_number": cxx_output.get("plate"),
            "slot": slot,
            "path": path
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'Error running allocation: {str(e)}'}), 500
    
# -------EXIT----------
@app.route('/exit')
def exit_vehicle():
    return render_template("exit_camera.html")

def capture_exit():
    number = "UNKNOWN"
    slot = "UNKNOWN"
    try:
        if os.path.exists('slots.txt'):
            with open('slots.txt', 'r') as f:
                lines = f.readlines()

            if lines:
                last_line = lines[-1].strip()
                if ':' in last_line:
                    slot, number = last_line.split(':', 1)
                    slot = slot.strip()
                    number = number.strip()

                # Remove the last line from slots.txt (i.e., the exited vehicle)
                with open('slots.txt', 'w') as f:
                    for line in lines:
                        if line.strip() != last_line:
                            f.write(line)

                # Log the exit
                log_exit_locally(number)

    except Exception as e:
        print(f"Error in capture_exit: {e}")

    return number, slot


@app.route('/log_exit', methods=['POST'])
def log_exit():
    data = request.get_json()
    plate = data.get('plate')

    if not plate:
        return jsonify({"error": "Plate number not provided"}), 400

    # Log exit into a separate file or update existing CSV
    exit_file = 'exit_logs.csv'
    file_exists = os.path.isfile(exit_file)
    with open(exit_file, 'a', newline='') as csvfile:
        fieldnames = ['Plate', 'Exit_Timestamp']
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        writer.writerow({
            'Plate': plate.strip().replace(" ", ""),
            'Exit_Timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })

    return jsonify({"message": "Exit logged successfully"}), 200


#-----Exit_Detect----
@app.route('/exit/detect', methods=['POST'])
def detect_exit_plate():
    try:
        data = request.get_json()
        if not data or 'image' not in data:
            return jsonify({'error': 'No image data provided'}), 400

        img_data = data['image'].split(',')[1]
        img_bytes = base64.b64decode(img_data)
        img_array = np.frombuffer(img_bytes, np.uint8)
        img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)

        if img is None:
            return jsonify({'error': 'Failed to decode image'}), 400

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        results = sorted(reader.readtext(gray), key=lambda x: x[2], reverse=True)

        plate = "UNKNOWN"
        for (_, text, prob) in results:
            if len(text) >= 4:
                plate = text.strip().replace(" ", "")
                break

        if plate == "UNKNOWN":
            return jsonify({'error': 'Could not detect plate'}), 400

        freed_slot = log_exit_locally(plate)

        if not freed_slot:
            return jsonify({'error': ' Vehicle not found in the parking lot'}), 404

        return jsonify({
            'plate': plate,
            'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'bill': 50,  
            'slot': freed_slot
        })

    except Exception as e:
        return jsonify({'error': f'Error during detection: {str(e)}'}), 500

def log_exit_locally(plate):
    updated_lines = []
    freed_slot = None

    plate = plate.upper().replace(" ", "").replace("]", "")

    with open('slots.txt', 'r') as file:
        lines = file.readlines()

    for line in lines:
        parts = line.strip().split()
        if len(parts) == 2:
            saved_plate = parts[0].replace("]", "")
            slot = parts[1]
            if saved_plate == plate:
                freed_slot = slot
                continue  
        updated_lines.append(line)

    if freed_slot:
        with open('slots.txt', 'w') as file:
            file.writelines(updated_lines)

    return freed_slot  # Will be None if plate not found


# -------- MAIN --------
print("Ready to run Flask app...")
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=8080)