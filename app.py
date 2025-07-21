import streamlit as st
import pandas as pd
import io
import os
from openai import OpenAI
from docx import Document
from docx.shared import Inches, Pt
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.section import WD_SECTION_START

# --- Configuration and Secrets ---
# IMPORTANT: Create a .streamlit/secrets.toml file in your project root
# and add your OpenAI API key and login credentials there.
# Example secrets.toml structure:
# [openai]
# api_key = "sk-proj-your_openai_api_key_here"
#
# [credentials]
# user1 = "User1@123"
# user2 = "User2@123"
# "ssoconsultants14@gmail.com" = "Sso@123"

# Load secrets
try:
    OPENAI_API_KEY = st.secrets["openai"]["api_key"] # Accessing the api_key from the [openai] section
    LOGIN_USERS = st.secrets["credentials"]          # Accessing the users from the [credentials] section
except KeyError as e:
    st.error(f"Secret not found: {e}. Please ensure your .streamlit/secrets.toml file is correctly configured with [openai] and [credentials] sections.")
    st.stop() # Stop the app if secrets are missing

# Initialize OpenAI client
client = OpenAI(api_key=OPENAI_API_KEY)

# --- Session State Initialization ---
# Initialize session state variables if they don't exist
if 'logged_in' not in st.session_state:
    st.session_state['logged_in'] = False
if 'current_username' not in st.session_state:
    st.session_state['current_username'] = None
if 'df' not in st.session_state:
    st.session_state['df'] = None
if 'data_summary' not in st.session_state:
    st.session_state['data_summary'] = ""
if 'messages' not in st.session_state:
    st.session_state['messages'] = []
if 'report_content' not in st.session_state:
    st.session_state['report_content'] = [] # List to store report sections
if 'user_goal' not in st.session_state:
    st.session_state['user_goal'] = "Not specified"
if 'uploaded_file_name' not in st.session_state: # To track if a new file is uploaded
    st.session_state['uploaded_file_name'] = None

# --- Helper Functions ---

def check_password():
    """
    Checks if the entered username and password match any of the ones in st.secrets['credentials'].
    This uses plain-text password comparison as per user's request,
    acknowledging the security implications.
    """
    if st.session_state['logged_in']:
        return True

    st.title("Login to Data Preprocessing Assistant")
    st.markdown("---")

    with st.form("login_form"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submit_button = st.form_submit_button("Login")

        if submit_button:
            # Check if the entered username exists in our stored users
            if username in LOGIN_USERS:
                # Compare the entered password with the stored plain-text password
                if password == LOGIN_USERS[username]:
                    st.session_state['logged_in'] = True
                    st.session_state['current_username'] = username # Store current logged-in username
                    st.rerun() # Rerun to switch to the main app
                else:
                    st.error("Invalid username or password.")
            else:
                st.error("Invalid username or password.")
    return False

def get_data_summary(df):
    """
    Generates a comprehensive summary of the DataFrame's characteristics.
    """
    summary = []
    summary.append(f"Dataset Overview:\n")
    summary.append(f"- Number of rows: {df.shape[0]}")
    summary.append(f"- Number of columns: {df.shape[1]}")
    summary.append(f"- Total duplicate rows: {df.duplicated().sum()}\n")

    summary.append("Column Details:\n")
    for col in df.columns:
        dtype = df[col].dtype
        missing_percent = df[col].isnull().sum() / len(df) * 100
        summary.append(f"  - Column '{col}':")
        summary.append(f"    - Data Type: {dtype}")
        summary.append(f"    - Missing Values: {missing_percent:.2f}%")

        if pd.api.types.is_numeric_dtype(df[col]):
            desc = df[col].describe()
            summary.append(f"    - Numerical Stats:")
            summary.append(f"      - Mean: {desc['mean']:.2f}")
            summary.append(f"      - Median: {df[col].median():.2f}")
            summary.append(f"      - Std Dev: {desc['std']:.2f}")
            summary.append(f"      - Min: {desc['min']:.2f}")
            summary.append(f"      - Max: {desc['max']:.2f}")
            summary.append(f"      - 25th Percentile: {desc['25%']:.2f}")
            summary.append(f"      - 75th Percentile: {desc['75%']:.2f}")
            summary.append(f"      - Skewness: {df[col].skew():.2f}")
            summary.append(f"      - Kurtosis: {df[col].kurt():.2f}")
        elif pd.api.types.is_object_dtype(df[col]) or pd.api.types.is_string_dtype(df[col]):
            unique_count = df[col].nunique()
            top_values = df[col].value_counts().head(5)
            summary.append(f"    - Categorical Stats:")
            summary.append(f"      - Unique Values (Cardinality): {unique_count}")
            if not top_values.empty:
                summary.append(f"      - Top 5 Values and Counts:")
                for val, count in top_values.items():
                    summary.append(f"        - '{val}': {count}")
        elif pd.api.types.is_datetime64_any_dtype(df[col]):
            summary.append(f"    - Date/Time Stats:")
            summary.append(f"      - Min Date: {df[col].min()}")
            summary.append(f"      - Max Date: {df[col].max()}")
        summary.append("") # Add a blank line for readability between columns

    return "\n".join(summary)

def generate_openai_response(prompt, model="gpt-3.5-turbo"):
    """
    Sends a prompt to the OpenAI API and returns the response.
    """
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are a data preprocessing expert. Provide clear, concise, and actionable advice. Include Python code snippets for suggested steps. Always ask the user about their goal for the dataset if not specified."},
                *st.session_state.messages, # Include full conversation history
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=1000,
            top_p=1.0,
            frequency_penalty=0.0,
            presence_penalty=0.0
        )
        return response.choices[0].message.content
    except Exception as e:
        st.error(f"Error communicating with OpenAI API: {e}")
        return "I'm sorry, I'm having trouble connecting to the AI. Please try again later."

def create_report_doc(report_data, logo_path="SsoLogo.jpg"):
    """
    Generates a Word document report from the accumulated report_data.
    """
    document = Document()

    # Add Logo to the report
    try:
        document.add_picture(logo_path, width=Inches(1.5))
        last_paragraph = document.paragraphs[-1]
        last_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    except FileNotFoundError:
        document.add_paragraph("SSO Consultants Logo (Image not found)")
    except Exception as e:
        document.add_paragraph(f"Error adding logo to report: {e}")

    document.add_heading('Dataset Preprocessing & Analysis Report', level=1)
    document.add_paragraph(f"Date Generated: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}")
    document.add_paragraph(f"User's Stated Goal: {st.session_state['user_goal']}")
    document.add_paragraph("\n")

    for section_title, content in report_data:
        document.add_heading(section_title, level=2)
        # Split content by lines to add as paragraphs or code blocks
        lines = content.split('\n')
        in_code_block = False
        code_block_content = []

        for line in lines:
            if line.strip().startswith("```"):
                if in_code_block:
                    # End of code block, add accumulated code
                    p = document.add_paragraph()
                    # Remove the language specifier from the first line of the code block
                    if code_block_content and code_block_content[0].strip().startswith("```"):
                        code_block_content[0] = code_block_content[0].strip()[3:]
                    p.add_run(f"\n{os.linesep.join(code_block_content)}\n").font.name = 'Consolas' # Example font for code
                    p.add_run("\n")
                    code_block_content = []
                in_code_block = not in_code_block
                continue # Skip the triple backticks line itself

            if in_code_block:
                code_block_content.append(line)
            else:
                document.add_paragraph(line)
        
        # If code block was open at the end (e.g., malformed markdown)
        if in_code_block and code_block_content:
            p = document.add_paragraph()
            # Remove the language specifier from the first line of the code block
            if code_block_content and code_block_content[0].strip().startswith("```"):
                code_block_content[0] = code_block_content[0].strip()[3:]
            p.add_run(f"\n{os.linesep.join(code_block_content)}\n").font.name = 'Consolas'
            p.add_run("\n")


    # Add a section for the footer in the report
    document.add_section(WD_SECTION_START.NEW_PAGE) # Start new page for footer/disclaimer
    footer_paragraph = document.add_paragraph()
    footer_run = footer_paragraph.add_run("SSO Consultants © 2025 | All Rights Reserved.")
    footer_run.font.size = Pt(9)
    footer_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER

    # Save document to a BytesIO object
    bio = io.BytesIO()
    document.save(bio)
    bio.seek(0) # Rewind the buffer to the beginning
    return bio

# --- Main Application Logic ---
def main_app():
    st.set_page_config(layout="wide", page_title="SSO Data Preprocessing Assistant")

    # Display Logo at the top of the main app
    st.image("SsoLogo.jpg", width=100) # Adjust width as needed
    st.title("Data Preprocessing Assistant")
    st.write(f"Welcome, {st.session_state.get('current_username', 'User')}!")


    st.sidebar.header("Upload Dataset")
    uploaded_file = st.sidebar.file_uploader("Choose a CSV or Excel file", type=["csv", "xlsx"])

    if uploaded_file is not None:
        # Check if a new file is uploaded or if df is not yet loaded
        if st.session_state['df'] is None or uploaded_file.name != st.session_state.get('uploaded_file_name'):
            st.session_state['messages'] = [] # Clear chat history for new file
            st.session_state['report_content'] = [] # Clear report content for new file
            st.session_state['user_goal'] = "Not specified" # Reset user goal
            st.session_state['uploaded_file_name'] = uploaded_file.name # Store file name to detect new upload

            try:
                if uploaded_file.name.endswith('.csv'):
                    df = pd.read_csv(uploaded_file)
                elif uploaded_file.name.endswith('.xlsx'):
                    df = pd.read_excel(uploaded_file)
                st.session_state['df'] = df

                st.subheader("Dataset Preview:")
                st.dataframe(df.head())
                st.write(f"Shape: {df.shape[0]} rows, {df.shape[1]} columns")

                # Generate data summary and store it
                st.session_state['data_summary'] = get_data_summary(df)
                st.session_state['report_content'].append(("Dataset Overview", st.session_state['data_summary']))

                # Initial prompt to OpenAI with data summary
                initial_ai_prompt = (
                    "Here is a detailed summary of the user's dataset:\n\n"
                    f"{st.session_state['data_summary']}\n\n"
                    "Based on this, what are the initial preprocessing considerations? "
                    "Please also ask the user about their primary goal (e.g., classification, regression, exploratory analysis) for this dataset."
                )
                with st.spinner("Analyzing data and generating initial insights..."):
                    initial_response = generate_openai_response(initial_ai_prompt)
                    st.session_state.messages.append({"role": "assistant", "content": initial_response})
                    st.session_state.report_content.append(("Initial Preprocessing Considerations", initial_response))

            except Exception as e:
                st.error(f"Error reading file: {e}. Please ensure it's a valid CSV or Excel file.")
                st.session_state['df'] = None # Reset df on error
                st.stop() # Stop further execution if file reading fails

    if st.session_state['df'] is not None:
        st.subheader("Chat with your Data Preprocessing Assistant")

        # Display chat messages from history
        for message in st.session_state.messages:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])

        # Chat input
        if prompt := st.chat_input("Ask about preprocessing or analysis..."):
            st.session_state.messages.append({"role": "user", "content": prompt})
            with st.chat_message("user"):
                st.markdown(prompt)

            # Update user goal if explicitly stated in the prompt
            if "my goal is" in prompt.lower() or "i want to do" in prompt.lower() or "my objective is" in prompt.lower():
                # Simple capture: take the whole prompt as the goal
                st.session_state['user_goal'] = prompt
                st.session_state.report_content.append(("User's Stated Goal", prompt))


            # Construct prompt for OpenAI, including data summary and full chat history
            full_prompt = (
                f"Dataset Summary:\n{st.session_state['data_summary']}\n\n"
                "Conversation History:\n" + "\n".join([f"{m['role']}: {m['content']}" for m in st.session_state.messages]) +
                f"\n\nUser's current message: {prompt}\n\n"
                "Based on the dataset summary and our conversation, provide tailored preprocessing advice, "
                "including explanations and relevant Python code snippets using pandas or scikit-learn. "
                "If the user has stated a goal, ensure your advice aligns with it."
            )

            with st.spinner("Generating response..."):
                response = generate_openai_response(full_prompt)
                st.session_state.messages.append({"role": "assistant", "content": response})
                st.session_state.report_content.append(("Chatbot Response", response)) # Log AI responses for report
                st.rerun() # Rerun to display the new message

        st.sidebar.markdown("---")
        st.sidebar.header("Report & Actions")

        # Download Report Button
        if st.sidebar.button("Generate & Download Report"):
            if st.session_state['df'] is not None:
                with st.spinner("Generating report..."):
                    report_buffer = create_report_doc(st.session_state['report_content'])
                    st.sidebar.download_button(
                        label="Download Report (.docx)",
                        data=report_buffer,
                        file_name="Data_Preprocessing_Report.docx",
                        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        key="download_report_button"
                    )
            else:
                st.sidebar.warning("Please upload a dataset first to generate a report.")

    # --- Footer ---
    st.markdown("---")
    # Centering the footer
    st.markdown(
        "<div style='text-align: center;'>"
        "SSO Consultants © 2025 | All Rights Reserved."
        "</div>",
        unsafe_allow_html=True
    )

    # Logout button in sidebar
    if st.sidebar.button("Logout"):
        st.session_state['logged_in'] = False
        st.session_state['current_username'] = None
        st.session_state['df'] = None
        st.session_state['data_summary'] = ""
        st.session_state['messages'] = []
        st.session_state['report_content'] = []
        st.session_state['user_goal'] = "Not specified"
        if 'uploaded_file_name' in st.session_state:
            del st.session_state['uploaded_file_name']
        st.rerun()

# --- Run the App ---
if not st.session_state['logged_in']:
    check_password()
else:
    main_app()

