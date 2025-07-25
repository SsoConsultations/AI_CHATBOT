import streamlit as st
import pandas as pd
import io
import os
from openai import OpenAI
from openai import AuthenticationError, APIConnectionError, RateLimitError, APIStatusError # Import specific OpenAI errors
import requests # Import requests for potential timeout errors
from docx import Document
from docx.shared import Inches, Pt
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.section import WD_SECTION_START
from docx.oxml.ns import qn # For setting table style
from docx.oxml import OxmlElement # For setting table style
import matplotlib.pyplot as plt
import seaborn as sns
import re # For parsing graph requests and markdown bolding
from scipy import stats # For statistical tests
import numpy as np # For numerical operations, especially for ANOVA SS calculations

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
# "ssoconsultants14@gmail.com" = "Sso@122" # Corrected password for ssoconsultants14@gmail.com


# Load secrets
try:
    OPENAI_API_KEY = st.secrets["openai"]["api_key"] # Accessing the api_key from the [openai] section
    LOGIN_USERS = st.secrets["credentials"]          # Accessing the users from the [credentials] section
except KeyError as e:
    st.error(f"Secret not found: {e}. Please ensure your .streamlit/secrets.toml file is correctly configured with [openai] and [credentials] sections.")
    st.stop() # Stop the app if secrets is missing

# Initialize OpenAI client (will be initialized after API key check)
client = None 

# --- Session State Initialization ---
# Initialize session state variables if they don't exist
if 'logged_in' not in st.session_state:
    st.session_state['logged_in'] = False
if 'current_username' not in st.session_state:
    st.session_state['current_username'] = None
if 'df' not in st.session_state:
    st.session_state['df'] = None
if 'data_summary_text' not in st.session_state: # Text summary for AI
    st.session_state['data_summary_text'] = ""
if 'data_summary_table' not in st.session_state: # Structured data for report table
    st.session_state['data_summary_table'] = []
if 'messages' not in st.session_state:
    st.session_state['messages'] = []
if 'report_content' not in st.session_state:
    st.session_state['report_content'] = [] # List to store report sections (structured)
if 'user_goal' not in st.session_state:
    st.session_state['user_goal'] = "Not specified"
if 'uploaded_file_name' not in st.session_state: # To track if a new file is uploaded
    st.session_state['uploaded_file_name'] = None
if 'openai_client_initialized' not in st.session_state:
    st.session_state['openai_client_initialized'] = False
if 'openai_client' not in st.session_state: # New: Store OpenAI client instance here
    st.session_state['openai_client'] = None
if 'debug_logs' not in st.session_state: # New: For in-app debug logs
    st.session_state['debug_logs'] = []

# --- Helper Functions ---

# Function to append debug messages to session state
def append_debug_log(message):
    st.session_state['debug_logs'].append(message)

def check_password():
    """
    Checks if the entered username and password match any of the ones in st.secrets['credentials'].
    This uses plain-text password comparison as per user's request,
    acknowledging the security implications.
    """
    if st.session_state['logged_in']:
        return True

    # --- Add Logo to Login Page (User Request 1) ---
    col1, col2 = st.columns([1, 5]) # Use columns for better alignment control
    with col1:
        # Check if the logo file exists
        script_dir = os.path.dirname(__file__) # Get directory of the current script
        logo_path = os.path.join(script_dir, "SsoLogo.jpg") # Construct full path
        if os.path.exists(logo_path):
            st.image(logo_path, width=100) # Adjust width as needed
        else:
            st.warning("SsoLogo.jpg not found. Please ensure it's in the same directory as app.py.")
    with col2:
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

def check_openai_api_key():
    """
    Attempts a simple OpenAI API call to verify the API key and store the client.
    Returns True if successful, False otherwise.
    """
    if st.session_state['openai_client_initialized'] and st.session_state['openai_client'] is not None:
        return True # Already checked and initialized

    try:
        # Initialize client and store in session state
        st.session_state['openai_client'] = OpenAI(api_key=OPENAI_API_KEY)
        
        # Attempt a simple call to verify the key
        response = st.session_state['openai_client'].chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": "Hello"}],
            max_tokens=5,
            stream=False, # Do not stream for this check
            timeout=10 # Add a timeout for the API call
        )
        st.session_state['openai_client_initialized'] = True
        st.success("OpenAI API key verified successfully! AI features are enabled.")
        return True
    except AuthenticationError:
        st.error("OpenAI API Key is invalid. Please check your .streamlit/secrets.toml file.")
        st.session_state['openai_client'] = None # Clear client on failure
        return False
    except APIConnectionError as e:
        st.error(f"Could not connect to OpenAI API: {e}. Please check your internet connection and firewall settings.")
        st.session_state['openai_client'] = None # Clear client on failure
        return False
    except RateLimitError:
        st.error("OpenAI API rate limit exceeded. Please try again later or check your OpenAI usage.")
        st.session_state['openai_client'] = None # Clear client on failure
        return False
    except requests.exceptions.Timeout:
        st.error("OpenAI API connection timed out during key verification. Please try again.")
        st.session_state['openai_client'] = None
        return False
    except APIStatusError as e: # Catch API specific status errors
        st.error(f"OpenAI API returned an error status: {e.status_code} - {e.response}")
        st.session_state['openai_client'] = None
        return False
    except Exception as e:
        st.error(f"An unexpected error occurred while checking OpenAI API key: {e}")
        st.session_state['openai_client'] = None # Clear client on failure
        return False


def get_data_summary(df):
    """
    Generates a comprehensive summary of the DataFrame's characteristics for AI and report.
    Returns (summary_text, column_details_for_table).
    """
    summary_text = []
    column_details_for_table = [] # For the report table

    summary_text.append(f"Dataset Overview:\n")
    summary_text.append(f"- Number of rows: {df.shape[0]}")
    summary_text.append(f"- Number of columns: {df.shape[1]}")
    summary_text.append(f"- Total duplicate rows: {df.duplicated().sum()}\n")

    column_details_for_table.append(["Column Name", "Data Type", "Missing %", "Stats Summary"]) # Table Header

    # Add a general heading for column details in the text summary
    summary_text.append("Column Details:\n")

    for col in df.columns:
        dtype = df[col].dtype
        missing_percent = df[col].isnull().sum() / len(df) * 100
        
        col_summary_text_parts = []
        col_table_summary = ""

        # For text summary
        col_summary_text_parts.append(f"  - Column '{col}':")
        col_summary_text_parts.append(f"    - Data Type: {dtype}")
        col_summary_text_parts.append(f"    - Missing Values: {missing_percent:.2f}%")

        if pd.api.types.is_numeric_dtype(df[col]):
            desc = df[col].describe()
            col_summary_text_parts.append(f"    - Numerical Stats:")
            col_summary_text_parts.append(f"      - Mean: {desc['mean']:.2f}")
            col_summary_text_parts.append(f"      - Median: {df[col].median():.2f}")
            col_summary_text_parts.append(f"      - Std Dev: {desc['std']:.2f}")
            col_summary_text_parts.append(f"      - Min: {desc['min']:.2f}")
            col_summary_text_parts.append(f"      - Max: {desc['max']:.2f}")
            col_summary_text_parts.append(f"      - 25th Percentile: {desc['25%']:.2f}")
            col_summary_text_parts.append(f"      - 75th Percentile: {desc['75%']:.2f}")
            col_summary_text_parts.append(f"      - Skewness: {df[col].skew():.2f}")
            col_summary_text_parts.append(f"      - Kurtosis: {df[col].kurt():.2f}")
            col_table_summary = (
                f"Mean: {desc['mean']:.2f}, Median: {df[col].median():.2f}, "
                f"Skew: {df[col].skew():.2f}, Missing: {missing_percent:.2f}%"
            )
        elif pd.api.types.is_object_dtype(df[col]) or pd.api.types.is_string_dtype(df[col]):
            unique_count = df[col].nunique()
            top_values = df[col].value_counts().head(3) # Limit to top 3 for table summary
            col_summary_text_parts.append(f"    - Categorical Stats:")
            col_summary_text_parts.append(f"      - Unique Values (Cardinality): {unique_count}")
            if not top_values.empty:
                top_vals_str = ", ".join([f"'{val}': {count}" for val, count in top_values.items()])
                col_summary_text_parts.append(f"      - Top 3 Values and Counts: {top_vals_str}")
                col_table_summary = f"Unique: {unique_count}, Top: {top_vals_str}, Missing: {missing_percent:.2f}%"
            else:
                col_table_summary = f"Unique: {unique_count}, Missing: {missing_percent:.2f}%"
        elif pd.api.types.is_datetime64_any_dtype(df[col]):
            col_summary_text_parts.append(f"    - Date/Time Stats:")
            col_summary_text_parts.append(f"      - Min Date: {df[col].min()}")
            col_summary_text_parts.append(f"      - Max Date: {df[col].max()}")
            col_table_summary = (
                f"Min Date: {df[col].min().strftime('%Y-%m-%d')}, "
                f"Max Date: {df[col].max().strftime('%Y-%m-%d')}, Missing: {missing_percent:.2f}%"
            )
        
        summary_text.append("\n".join(col_summary_text_parts))
        summary_text.append("") # Add a blank line for readability between columns in text summary
        column_details_for_table.append([col, str(dtype), f"{missing_percent:.2f}%", col_table_summary])

    return "\n".join(summary_text), column_details_for_table

def generate_openai_response(prompt, model="gpt-3.5-turbo"):
    """
    Sends a prompt to the OpenAI API and returns the response.
    Explicitly instructs the model NOT to provide Python code snippets or markdown formatting.
    """
    # Retrieve client from session state
    client_instance = st.session_state.get('openai_client')

    if client_instance is None or not st.session_state['openai_client_initialized']:
        append_debug_log("DEBUG: OpenAI client not initialized or missing.") # Debug print
        return "AI features are not enabled due to API key issues. Please check your OpenAI API key."

    try:
        # Filter messages to only include text-based content for the AI API call
        api_messages_history = []
        # Only send the last 5 relevant messages to save tokens and maintain context
        for msg in st.session_state.messages[-5:]:
            if msg["role"] in ["user", "assistant"]: # Only include user and assistant text messages
                api_messages_history.append({"role": msg["role"], "content": msg["content"]})
        
        # Add the current user prompt
        api_messages_history.append({"role": "user", "content": prompt})

        append_debug_log(f"DEBUG: Sending prompt to OpenAI (max_tokens=2000):\n{api_messages_history}\n---") # Debug print
        
        # --- NEW: More robust API call with specific error handling and timeout ---
        try:
            response = client_instance.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "You are a data preprocessing expert. Provide clear, concise, and actionable advice. Do NOT use any markdown formatting (like bolding, italics, code blocks) in your responses. Focus on natural language explanations and interpretations. Always ask the user about their goal for the dataset if not specified, or if a graph is generated, provide an interpretation of that graph. Keep responses concise and to the point."},
                    *api_messages_history
                ],
                temperature=0.7,
                max_tokens=2000,
                top_p=1.0,
                frequency_penalty=0.0,
                presence_penalty=0.0,
                timeout=30 # Increased timeout to 30 seconds for API calls
            )
            ai_response_content = response.choices[0].message.content
            append_debug_log(f"DEBUG: Raw OpenAI response received:\n{ai_response_content}\n---") # Debug print
            return ai_response_content
        except AuthenticationError as e:
            append_debug_log(f"DEBUG: API Call AuthenticationError: {e}") # Debug print
            st.error("OpenAI API Key is invalid during chat. Please check your .streamlit/secrets.toml file.")
            return "I'm sorry, my connection to the AI failed due to an invalid API key. Please contact support."
        except APIConnectionError as e:
            append_debug_log(f"DEBUG: API Call ConnectionError: {e}") # Debug print
            st.error(f"Could not connect to OpenAI API during chat: {e}. Please check your internet connection and firewall settings.")
            return "I'm sorry, I'm having trouble connecting to the AI. Please check your internet connection and try again."
        except RateLimitError as e:
            append_debug_log(f"DEBUG: API Call RateLimitError: {e}") # Debug print
            st.error("OpenAI API rate limit exceeded during chat. Please try again in a moment.")
            return "I'm sorry, the AI is experiencing high demand. Please try again in a moment."
        except requests.exceptions.Timeout as e: # Catch specific requests timeout
            append_debug_log(f"DEBUG: API Call Timeout: {e}") # Debug print
            st.error("OpenAI API request timed out. The server took too long to respond. Please try again.")
            return "I'm sorry, the AI took too long to respond. Please try again in a moment."
        except APIStatusError as e: # Catch API specific status errors (e.g., 4xx, 5xx from OpenAI)
            append_debug_log(f"DEBUG: API Call Status Error: {e.status_code} - {e.response}") # Debug print
            st.error(f"OpenAI API returned an error status: {e.status_code}. Please try again later.")
            return f"I'm sorry, the AI encountered an error ({e.status_code}). Please try again later."
        except Exception as e: # Catch any other unexpected errors during the API call
            append_debug_log(f"DEBUG: Unexpected Exception during API call: {e}") # Debug print
            st.error(f"An unexpected error occurred during AI API call: {e}")
            st.exception(e) # Display the full traceback in the Streamlit UI for debugging
            return "I'm sorry, an unexpected error occurred while communicating with the AI. Please try again later."

    except Exception as e: # This outer catch block is for errors *before* the API call (e.g., client not initialized)
        append_debug_log(f"DEBUG: General Exception in generate_openai_response (outer block): {e}") # Debug print
        st.error(f"An unexpected error occurred while generating the AI response: {e}")
        st.exception(e) # Display the full traceback in the Streamlit UI for debugging
        return "I'm sorry, an unexpected error occurred while generating the AI response. Please try again later."


def create_report_doc(report_data, logo_path="SsoLogo.jpg"):
    """
    Generates a Word document report from the accumulated report_data.
    """
    document = Document()

    # Add Logo to the report
    try:
        # Construct full path to logo
        script_dir = os.path.dirname(__file__)
        full_logo_path = os.path.join(script_dir, logo_path)
        document.add_picture(full_logo_path, width=Inches(1.5))
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

    for item in report_data:
        item_type = item.get("type")
        content = item.get("content")

        if item_type == "heading":
            heading = document.add_heading('', level=item.get("level", 2))
            run = heading.add_run(content)
            run.bold = True # Ensure headings are bold
        elif item_type == "text":
            # REVISED LOGIC FOR TEXT CONTENT TO HANDLE NUMBERED LISTS PROPERLY
            lines = content.split('\n')
            for line in lines:
                line = line.strip() # Remove leading/trailing whitespace
                if not line: # Skip empty lines
                    continue

                p = document.add_paragraph()
                # Check if it's a numbered list item
                numbered_list_match = re.match(r'^(\d+\.\s*)(.*)', line)
                if numbered_list_match:
                    # Add the number part (e.g., "1. ")
                    p.add_run(numbered_list_match.group(1))
                    
                    # Get the rest of the line after the number
                    rest_of_line = numbered_list_match.group(2)
                    
                    # This regex is for finding markdown bolding within the rest_of_line
                    parts = re.split(r'(\*\*.*?\*\*)', rest_of_line)
                    for part in parts:
                        if part is None or part == '':
                            continue
                        if part.startswith('**') and part.endswith('**'):
                            run = p.add_run(part[2:-2])
                            run.bold = True
                        else:
                            p.add_run(part)
                else:
                    # Not a numbered list item, just add as a regular paragraph
                    # Also check for markdown bolding in regular paragraphs
                    parts = re.split(r'(\*\*.*?\*\*)', line)
                    for part in parts:
                        if part is None or part == '':
                            continue
                        if part.startswith('**') and part.endswith('**'):
                            run = p.add_run(part[2:-2])
                            run.bold = True
                        else:
                            p.add_run(part)

        elif item_type == "table":
            # Original headers and rows from st.session_state['data_summary_table']
            original_headers = item.get("headers", []) # ['Column Name', 'Data Type', 'Missing %', 'Stats Summary']
            original_rows = item.get("rows", []) # [['ID', 'int64', '0.00%', 'Mean: ...'], ...]

            if original_headers and original_rows:
                # Add a specific sub-heading for the table
                table_heading = document.add_heading('', level=3)
                run = table_heading.add_run("Column Details Overview")
                run.bold = True

                # Define new headers for the Word table
                # We want "Column Name\n(Type)", "Missing %", "Stats Summary"
                new_table_headers = ["Column Name\n(Type)", original_headers[2], original_headers[3]]
                
                table = document.add_table(rows=1, cols=len(new_table_headers))
                table.style = 'Table Grid' # Apply a basic table style

                # Add new headers to the Word table
                hdr_cells = table.rows[0].cells
                for i, header_text in enumerate(new_table_headers):
                    hdr_cells[i].text = header_text
                    # Set header bold
                    for paragraph in hdr_cells[i].paragraphs:
                        for run in paragraph.runs:
                            run.bold = True

                # Data type short form mapping
                dtype_map = {
                    'int64': 'num',
                    'float64': 'num',
                    'object': 'str',
                    'category': 'str',
                    'datetime64[ns]': 'date', # Pandas datetime dtype often includes [ns]
                    'datetime64': 'date', # General datetime
                    'bool': 'bool' # Boolean type
                }

                # Add rows, transforming the first two columns
                for row_data in original_rows:
                    # row_data is like ['ID', 'int64', '0.00%', 'Mean: ...']
                    col_name = row_data[0]
                    original_dtype = row_data[1]
                    # Get short form, default to 'other' if not in map
                    short_dtype = dtype_map.get(original_dtype, 'other') 
                    combined_col_info = f"{col_name}\n({short_dtype})"
                    
                    # Create the new row for the Word table
                    new_row_for_table = [combined_col_info, row_data[2], row_data[3]] # Use original missing % and stats summary

                    row_cells = table.add_row().cells
                    for i, cell_data in enumerate(new_row_for_table):
                        row_cells[i].text = str(cell_data)

        elif item_type == "image":
            # Image data is expected as BytesIO object
            try:
                document.add_picture(content, width=Inches(6)) # Adjust width as needed
                last_paragraph = document.paragraphs[-1]
                last_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
                caption = item.get("caption", "")
                if caption:
                    document.add_paragraph(caption, style='Caption') # Add caption style if available
            except Exception as e:
                document.add_paragraph(f"Error adding image to report: {e}")
        
        elif item_type == "stat_table": # For structured statistical tables
            table_title = item.get("title")
            df_to_add = item.get("dataframe")

            if table_title and df_to_add is not None:
                document.add_paragraph(table_title, style='Heading 4') # Use a sub-heading for the table
                
                # Create a new table in the document
                table = document.add_table(rows=df_to_add.shape[0] + 1, cols=df_to_add.shape[1])
                table.style = 'Table Grid' # Apply a basic table style

                # Add header row
                for i, col_name in enumerate(df_to_add.columns):
                    cell = table.cell(0, i)
                    cell.text = str(col_name)
                    for paragraph in cell.paragraphs:
                        for run in paragraph.runs:
                            run.bold = True

                # Add data rows
                for r_idx, row_data in enumerate(df_to_add.itertuples(index=False), start=1):
                    for c_idx, cell_value in enumerate(row_data):
                        # Format floats to 4 decimal places
                        table.cell(r_idx, c_idx).text = str(f"{cell_value:.4f}" if isinstance(cell_value, (float)) else cell_value)
            
        document.add_paragraph("\n") # Add a blank line after each section for spacing

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

def generate_and_display_graph(df, graph_type, columns):
    """
    Generates a graph (histogram, box plot, scatter plot, correlation heatmap, bar chart)
    and returns its BytesIO buffer and a description.
    """
    img_buffer = io.BytesIO()
    graph_desc = ""
    plt.figure(figsize=(10, 6)) # Set a default figure size

    try:
        if graph_type == "histogram":
            if not columns or not pd.api.types.is_numeric_dtype(df[columns[0]]):
                return None, "Unsupported graph type requested or invalid column for histogram. Please select a numerical column."
            sns.histplot(df[columns[0]].dropna(), kde=True)
            plt.title(f'Histogram of {columns[0]}')
            plt.xlabel(columns[0])
            plt.ylabel('Frequency')
            graph_desc = f"A histogram for the '{columns[0]}' column was generated. It shows the distribution of values for this numerical feature."
        
        elif graph_type == "box_plot":
            if not columns or not pd.api.types.is_numeric_dtype(df[columns[0]]):
                return None, "Unsupported graph type requested or invalid column for box plot. Please select a numerical column."
            sns.boxplot(y=df[columns[0]].dropna())
            plt.title(f'Box Plot of {columns[0]}')
            plt.ylabel(columns[0])
            graph_desc = f"A box plot for the '{columns[0]}' column was generated. It displays the distribution, median, quartiles, and potential outliers of this numerical feature."

        elif graph_type == "scatter_plot":
            if len(columns) != 2 or not pd.api.types.is_numeric_dtype(df[columns[0]]) or not pd.api.types.is_numeric_dtype(df[columns[1]]):
                return None, "Unsupported graph type requested or invalid columns for scatter plot. Please select two numerical columns."
            sns.scatterplot(x=df[columns[0]], y=df[columns[1]])
            plt.title(f'Scatter Plot of {columns[0]} vs {columns[1]}')
            plt.xlabel(columns[0])
            plt.ylabel(columns[1])
            graph_desc = f"A scatter plot of '{columns[0]}' versus '{columns[1]}' was generated. It visualizes the relationship between these two numerical features."

        elif graph_type == "correlation_heatmap":
            numerical_cols = df.select_dtypes(include=['number']).columns
            if numerical_cols.empty:
                return None, "No numerical columns found to generate a correlation heatmap."
            correlation_matrix = df[numerical_cols].corr()
            sns.heatmap(correlation_matrix, annot=True, cmap='coolwarm', fmt=".2f")
            plt.title("Correlation Matrix of Numerical Features")
            graph_desc = "A correlation heatmap of all numerical features was generated. It displays the pairwise correlation coefficients, indicating the strength and direction of linear relationships between variables."

        elif graph_type == "bar_chart":
            if not columns or not (pd.api.types.is_object_dtype(df[columns[0]]) or pd.api.types.is_string_dtype(df[columns[0]]) or pd.api.types.is_categorical_dtype(df[columns[0]])):
                return None, "Unsupported graph type requested or invalid column for bar chart. Please select a categorical column."
            
            # For bar charts, limit to top 10 categories to avoid clutter
            value_counts = df[columns[0]].value_counts().head(10)
            if value_counts.empty:
                return None, f"No data found for bar chart in column '{columns[0]}'."

            sns.barplot(x=value_counts.index, y=value_counts.values)
            plt.title(f'Frequency of {columns[0]} (Top {len(value_counts)})')
            plt.xlabel(columns[0])
            plt.ylabel('Count')
            plt.xticks(rotation=45, ha='right') # Rotate labels for readability
            plt.tight_layout() # Adjust layout to prevent labels overlapping
            graph_desc = f"A bar chart showing the frequency of top categories in '{columns[0]}' was generated. It helps visualize the distribution of categorical values."
        
        else:
            return None, "Unsupported graph type requested. Please ask for a histogram, box plot, scatter plot, correlation heatmap, or bar chart."

        plt.savefig(img_buffer, format='png', bbox_inches='tight') # Save to buffer
        img_buffer.seek(0) # Rewind the buffer
        plt.close() # Close the plot to free memory
        return img_buffer, graph_desc

    except Exception as e:
        plt.close() # Ensure plot is closed even on error
        return None, f"An error occurred while generating the graph: {e}. Please check your column selections and data."


def perform_statistical_test(df, test_type, col1, col2=None):
    """
    Performs the selected statistical test and returns the results as a formatted string
    and optionally a pandas DataFrame(s) for structured output.
    Returns (results_str, structured_results_for_ui, error_message).
    structured_results_for_ui will be None if not applicable or on error, or a DataFrame/tuple of DataFrames.
    """
    results_str = ""
    structured_results_for_ui = None # New: To hold structured results for UI display (e.g., tuple of DFs)
    error_message = None

    try:
        if test_type == "anova":
            append_debug_log(f"DEBUG ANOVA: col1={col1}, col2={col2}")
            append_debug_log(f"DEBUG ANOVA: is_numeric_dtype(df[{col1}])={pd.api.types.is_numeric_dtype(df[col1])}")
            append_debug_log(f"DEBUG ANOVA: is_categorical_dtype(df[{col2}])={pd.api.types.is_categorical_dtype(df[col2])} | is_object_dtype(df[{col2}])={pd.api.types.is_object_dtype(df[col2])} | is_string_dtype(df[{col2}])={pd.api.types.is_string_dtype(df[col2])}")
            if not pd.api.types.is_numeric_dtype(df[col1]):
                error_message = f"ANOVA: Dependent variable '{col1}' must be numerical."
            elif not (pd.api.types.is_object_dtype(df[col2]) or pd.api.types.is_string_dtype(df[col2]) or pd.api.types.is_categorical_dtype(df[col2])):
                error_message = f"ANOVA: Independent variable '{col2}' must be categorical."
            else:
                # Drop NaNs from both columns for consistent analysis
                clean_df = df[[col1, col2]].dropna()
                
                groups = [clean_df[col1][clean_df[col2] == g] for g in clean_df[col2].unique()]
                append_debug_log(f"DEBUG ANOVA: unique_groups={clean_df[col2].unique()}, len(groups)={len(groups)}")
                
                if len(groups) < 2:
                    error_message = f"ANOVA: Independent variable '{col2}' needs at least 2 distinct groups."
                elif any(len(g) == 0 for g in groups):
                    error_message = f"ANOVA: Some groups in '{col2}' have no data for '{col1}' after dropping NaNs."
                else:
                    # Perform ANOVA using scipy
                    f_statistic_scipy, p_value_scipy = stats.f_oneway(*groups)

                    # Calculate Sum of Squares (SS) and Degrees of Freedom (df) for ANOVA table
                    grand_mean = clean_df[col1].mean()
                    
                    # Sum of Squares Total (SST)
                    sst = np.sum((clean_df[col1] - grand_mean)**2)
                    df_total = len(clean_df) - 1

                    # Sum of Squares Between (SSB)
                    ssb = 0
                    for g in clean_df[col2].unique():
                        group_data = clean_df[col1][clean_df[col2] == g]
                        ssb += len(group_data) * (group_data.mean() - grand_mean)**2
                    df_between = len(clean_df[col2].unique()) - 1

                    # Sum of Squares Within (SSW)
                    ssw = np.sum((clean_df[col1] - clean_df.groupby(col2)[col1].transform('mean'))**2)
                    df_within = len(clean_df) - len(clean_df[col2].unique())

                    # Mean Squares
                    msb = ssb / df_between if df_between > 0 else np.nan
                    msw = ssw / df_within if df_within > 0 else np.nan

                    # F-statistic (re-calculated to match SS/MS for table consistency, should be close to scipy's)
                    f_stat_calculated = msb / msw if msw > 0 else np.nan

                    # Create ANOVA Summary DataFrame
                    anova_summary_data = {
                        'Source of Variation': ['Between Groups', 'Within Groups', 'Total'],
                        'Sum of Squares (SS)': [ssb, ssw, sst],
                        'df': [df_between, df_within, df_total],
                        'Mean Squares (MS)': [msb, msw, np.nan], # MS for Total is not typically reported
                        'F': [f_stat_calculated, np.nan, np.nan], # F-stat only for Between Groups
                        'P-value': [p_value_scipy, np.nan, np.nan] # P-value only for Between Groups
                    }
                    anova_df = pd.DataFrame(anova_summary_data)
                    
                    results_str = (
                        f"ANOVA Test Results for '{col1}' by '{col2}':\n"
                        f"  F-statistic: {f_statistic_scipy:.4f}\n" # Use scipy's F-stat for the initial text summary
                        f"  P-value: {p_value_scipy:.4f}\n"
                        "Interpretation will be provided by the AI."
                    )
                    structured_results_for_ui = anova_df # Return the single DataFrame

        elif test_type == "independent_t_test":
            append_debug_log(f"DEBUG Independent T-test: col1={col1}, col2={col2}")
            append_debug_log(f"DEBUG Independent T-test: df[{col1}].dtype={df[col1].dtype}, df[{col2}].dtype={df[col2].dtype}")
            if not pd.api.types.is_numeric_dtype(df[col1]):
                error_message = f"Independent T-test: Numerical variable '{col1}' must be numerical."
            elif not (pd.api.types.is_object_dtype(df[col2]) or pd.api.types.is_string_dtype(df[col2]) or pd.api.types.is_categorical_dtype(df[col2])):
                error_message = f"Independent T-test: Grouping variable '{col2}' must be categorical."
            else:
                unique_groups = df[col2].unique()
                append_debug_log(f"DEBUG Independent T-test: unique_groups={unique_groups}, len(unique_groups)={len(unique_groups)}")
                if len(unique_groups) != 2:
                    error_message = f"Independent T-test: Grouping variable '{col2}' must have exactly 2 distinct groups. Found {len(unique_groups)}."
                else:
                    group1_name = unique_groups[0]
                    group2_name = unique_groups[1]
                    group1_data = df[col1][df[col2] == group1_name].dropna()
                    group2_data = df[col1][df[col2] == group2_name].dropna()
                    append_debug_log(f"DEBUG Independent T-test: group1_data_len={len(group1_data)}, group2_data_len={len(group2_data)}")
                    if len(group1_data) == 0 or len(group2_data) == 0:
                        error_message = f"Independent T-test: One or both groups have no data for '{col1}' after dropping NaNs."
                    else:
                        t_statistic, p_value = stats.ttest_ind(group1_data, group2_data, equal_var=True) # Assuming equal variances for this specific test
                        # Prepare structured results (Excel-like output)
                        group_stats_data = {
                            'Group': [group1_name, group2_name],
                            'N': [len(group1_data), len(group2_data)],
                            'Mean': [group1_data.mean(), group2_data.mean()],
                            'Std. Deviation': [group1_data.std(), group2_data.std()]
                        }
                        group_stats_df = pd.DataFrame(group_stats_data)

                        test_results_data = {
                            'Statistic': ['T-statistic', 'P-value'],
                            'Value': [t_statistic, p_value]
                        }
                        test_results_df = pd.DataFrame(test_results_data)
                        
                        results_str = (
                            f"Independent T-test (Equal Variances Assumed) Results for '{col1}' by '{col2}' ({group1_name} vs {group2_name}):\n"
                            f"  T-statistic: {t_statistic:.4f}\n"
                            f"  P-value: {p_value:.4f}\n"
                            "Interpretation will be provided by the AI."
                        )
                        # Store both dataframes in a tuple for structured_results_for_ui
                        structured_results_for_ui = (group_stats_df, test_results_df)

        elif test_type == "chi_squared_test":
            append_debug_log(f"DEBUG Chi-squared: col1={col1}, col2={col2}")
            append_debug_log(f"DEBUG Chi-squared: df[{col1}].dtype={df[col1].dtype}, df[{col2}].dtype={df[col2].dtype}")
            if not (pd.api.types.is_object_dtype(df[col1]) or pd.api.types.is_string_dtype(df[col1]) or pd.api.types.is_categorical_dtype(df[col1])):
                error_message = f"Chi-squared: Column 1 '{col1}' must be categorical."
            elif not (pd.api.types.is_object_dtype(df[col2]) or pd.api.types.is_string_dtype(df[col2]) or pd.api.types.is_categorical_dtype(df[col2])):
                error_message = f"Chi-squared: Column 2 '{col2}' must be categorical."
            else:
                contingency_table = pd.crosstab(df[col1], df[col2])
                append_debug_log(f"DEBUG Chi-squared: contingency_table_shape={contingency_table.shape}, sum={contingency_table.sum().sum()}")
                if contingency_table.empty or contingency_table.sum().sum() == 0:
                    error_message = f"Chi-squared: No valid data to form a contingency table for '{col1}' and '{col2}'."
                else:
                    chi2, p_value, dof, expected = stats.chi2_contingency(contingency_table)
                    # Create DataFrames for Observed and Expected tables
                    observed_df = contingency_table
                    expected_df = pd.DataFrame(expected, index=contingency_table.index, columns=contingency_table.columns)

                    results_str = (
                        f"Chi-squared Test Results for '{col1}' and '{col2}':\n"
                        f"  Chi-squared statistic: {chi2:.4f}\n"
                        f"  P-value: {p_value:.4f}\n"
                        f"  Degrees of Freedom (dof): {dof}\n"
                        "Interpretation will be provided by the AI."
                    )
                    structured_results_for_ui = (observed_df, expected_df, chi2, p_value, dof) # Return multiple values

        elif test_type == "pearson_correlation":
            append_debug_log(f"DEBUG Pearson: col1={col1}, col2={col2}")
            if not pd.api.types.is_numeric_dtype(df[col1]) or not pd.api.types.is_numeric_dtype(df[col2]):
                error_message = "Pearson Correlation: Both columns must be numerical."
            else:
                clean_df = df[[col1, col2]].dropna()
                if len(clean_df) < 2:
                    error_message = "Pearson Correlation: Not enough valid data points after dropping NaNs to calculate correlation."
                else:
                    correlation = clean_df[col1].corr(clean_df[col2])
                    # To get p-value for Pearson correlation, typically use pearsonr from scipy.stats
                    # It returns (correlation coefficient, 2-tailed p-value)
                    r_value, p_value = stats.pearsonr(clean_df[col1], clean_df[col2])
                    
                    # Also calculate covariance
                    covariance = clean_df[col1].cov(clean_df[col2])
                    n_obs = len(clean_df) # Number of observations used in calculation

                    # Create Correlation Results table
                    corr_results_data = {
                        'Statistic': ['Pearson Correlation (r)', 'Covariance', 'P-value', 'Number of Observations (N)'],
                        'Value': [r_value, covariance, p_value, n_obs]
                    }
                    corr_results_df = pd.DataFrame(corr_results_data)

                    results_str = (
                        f"Pearson Correlation Results for '{col1}' and '{col2}':\n"
                        f"  Pearson Correlation (r): {r_value:.4f}\n"
                        f"  P-value: {p_value:.4f}\n"
                        f"  Covariance: {covariance:.4f}\n"
                        f"  Number of Observations (N): {n_obs}\n"
                        "Interpretation will be provided by the AI."
                    )
                    structured_results_for_ui = corr_results_df # Return the single DataFrame

        else:
            error_message = "Unsupported statistical test. Please choose from 'anova', 'independent_t_test', 'chi_squared_test', or 'pearson_correlation'."

    except Exception as e:
        error_message = f"An error occurred during the statistical test: {e}. Please check your column selections and data."
        append_debug_log(f"DEBUG: Error in perform_statistical_test: {e}") # Debug print

    return results_str, structured_results_for_ui, error_message

# --- Main App Logic ---
# Check if user is logged in
if not st.session_state['logged_in']:
    check_password()
else:
    # After successful login, ensure OpenAI client is initialized
    # This ensures that if the app reruns for other reasons after login,
    # the client is verified once.
    if not st.session_state['openai_client_initialized']:
        append_debug_log("DEBUG: Attempting OpenAI API key check after login.") # Debug print
        if not check_openai_api_key():
            st.warning("AI features are not available. Please fix your OpenAI API key configuration.")
            # Do not proceed with main app if API key check fails after login,
            # but allow user to interact with other parts if desired.
            # st.stop() # Removed st.stop() to allow non-AI features to load

    st.set_page_config(layout="wide") # Set page layout to wide
    st.title(f"Data Preprocessing Assistant")
    st.markdown("Welcome, **{}**!".format(st.session_state['current_username'])) # Display logged-in username

    # --- Sidebar for Navigation and Controls ---
    st.sidebar.header("Navigation")

    # --- Add Logout Button to Sidebar (User Request 2) ---
    if st.sidebar.button("Logout", key="sidebar_logout_button"):
        st.session_state['logged_in'] = False
        st.session_state['current_username'] = None
        st.session_state['df'] = None
        st.session_state['data_summary_text'] = ""
        st.session_state['data_summary_table'] = []
        st.session_state['messages'] = []
        st.session_state['report_content'] = []
        st.session_state['user_goal'] = "Not specified"
        st.session_state['uploaded_file_name'] = None
        st.session_state['openai_client_initialized'] = False
        st.session_state['openai_client'] = None
        st.session_state['debug_logs'] = []
        st.rerun()

    uploaded_file = st.sidebar.file_uploader("Upload your dataset (CSV, Excel)", type=["csv", "xlsx"])

    if uploaded_file is not None:
        current_file_name = uploaded_file.name
        if st.session_state['df'] is None or st.session_state['uploaded_file_name'] != current_file_name:
            with st.spinner("Loading dataset..."):
                try:
                    if uploaded_file.name.endswith('.csv'):
                        df = pd.read_csv(uploaded_file)
                    elif uploaded_file.name.endswith('.xlsx'):
                        df = pd.read_excel(uploaded_file)
                    st.session_state['df'] = df
                    st.session_state['uploaded_file_name'] = current_file_name
                    st.success("Dataset loaded successfully!")
                    append_debug_log(f"DEBUG: Dataset '{current_file_name}' loaded.") # Debug print

                    # Clear previous analysis results when new file is uploaded
                    st.session_state['data_summary_text'] = ""
                    st.session_state['data_summary_table'] = []
                    st.session_state['messages'] = []
                    st.session_state['report_content'] = []
                    st.session_state['user_goal'] = "Not specified"
                    st.session_state['debug_logs'] = [] # Clear logs on new upload
                    append_debug_log("DEBUG: Session state cleared for new dataset.") # Debug print

                    # Generate initial summary for the new dataset
                    summary_text, summary_table_data = get_data_summary(df)
                    st.session_state['data_summary_text'] = summary_text
                    st.session_state['data_summary_table'] = summary_table_data
                    
                    # Add initial data summary to report content
                    st.session_state['report_content'].append({"type": "heading", "content": "1. Dataset Overview", "level": 2})
                    st.session_state['report_content'].append({"type": "text", "content": summary_text})
                    st.session_state['report_content'].append({"type": "table", "headers": summary_table_data[0], "rows": summary_table_data[1:]})

                    append_debug_log("DEBUG: Initial data summary generated and stored in session state.") # Debug print
                    st.rerun() # Rerun to display the loaded data and summary
                except Exception as e:
                    st.error(f"Error loading file: {e}. Please ensure it's a valid CSV or Excel file.")
                    append_debug_log(f"DEBUG: Error loading file: {e}") # Debug print
                    st.session_state['df'] = None # Clear df on error
                    st.session_state['uploaded_file_name'] = None # Clear file name on error
        
        df = st.session_state['df'] # Ensure df is always retrieved from session state

        # --- Display Dataset Overview (User Request 3) ---
        if st.session_state['data_summary_text']:
            st.subheader("Dataset Overview")
            st.write(st.session_state['data_summary_text']) # Display text summary
            
            # Display summary table as a Streamlit DataFrame
            if st.session_state['data_summary_table']:
                # The first row of data_summary_table is headers
                headers = st.session_state['data_summary_table'][0]
                rows = st.session_state['data_summary_table'][1:]
                display_df = pd.DataFrame(rows, columns=headers)
                st.dataframe(display_df, use_container_width=True)


        st.subheader("Raw Data Preview")
        st.dataframe(df.head(10), use_container_width=True) # Display first 10 rows

        # --- AI Assistant Section ---
        st.header("AI Data Assistant")
        user_question = st.text_area("Ask a question about your dataset or suggest a preprocessing step:", key="user_question_input")

        if st.session_state['openai_client_initialized'] and st.session_state['openai_client'] is not None:
            if st.button("Get AI Suggestion", key="get_ai_suggestion_button"):
                if user_question:
                    with st.spinner("Getting AI suggestion..."):
                        # Add user's question to messages before generating response
                        st.session_state.messages.append({"role": "user", "content": user_question})

                        # Context for the AI: Data summary and column names
                        ai_context = f"Here is the dataset summary:\n{st.session_state['data_summary_text']}\n\nColumn names: {', '.join(df.columns.tolist())}"
                        
                        full_prompt = f"Dataset Context: {ai_context}\n\nUser Question: {user_question}\n\nBased on the user's question and the dataset context, provide clear, concise, and actionable advice. DO NOT include any code snippets or markdown formatting (like bolding, italics, code blocks) in your response. Just plain text. If the user asks for an interpretation of a graph or a statistical test, provide that. If the user asks about their goal, address it."
                        
                        ai_response = generate_openai_response(full_prompt)
                        st.session_state.messages.append({"role": "assistant", "content": ai_response})
                        append_debug_log(f"DEBUG: AI response generated for user question.") # Debug print
                        st.rerun() # Rerun to display the new message
                else:
                    st.warning("Please enter a question for the AI assistant.")
        else:
            st.warning("AI features are disabled due to an invalid or unverified OpenAI API key.")


        # Display chat messages
        for message in st.session_state.messages:
            with st.chat_message(message["role"]):
                st.write(message["content"])

        # --- Statistical Analysis & Graph Generation Section ---
        st.header("Statistical Analysis & Visualizations")

        # Select columns for analysis
        all_columns = df.columns.tolist()
        selected_cols = st.multiselect("Select columns for analysis/visualization:", all_columns, key="selected_cols_multiselect")

        # Graph Generation
        st.subheader("Generate Graphs")
        graph_options = ["None", "histogram", "box_plot", "scatter_plot", "correlation_heatmap", "bar_chart"]
        selected_graph_type = st.selectbox("Select Graph Type:", graph_options, key="graph_type_select")

        if selected_graph_type != "None" and selected_cols:
            if st.button(f"Generate {selected_graph_type.replace('_', ' ').title()}"):
                img_buffer, graph_description = generate_and_display_graph(df, selected_graph_type, selected_cols)
                if img_buffer:
                    st.image(img_buffer, caption=graph_description, use_column_width=True)
                    # Add graph to report content
                    st.session_state['report_content'].append({"type": "heading", "content": f"Graph: {selected_graph_type.replace('_', ' ').title()} - {', '.join(selected_cols)}", "level": 3})
                    st.session_state['report_content'].append({"type": "image", "content": img_buffer.getvalue(), "caption": graph_description}) # Pass bytes content
                    # Ask AI for interpretation of the graph
                    if st.session_state['openai_client_initialized']:
                        ai_graph_prompt = f"A {selected_graph_type.replace('_', ' ')} was generated for columns {', '.join(selected_cols)}. Here is its description: {graph_description}. Provide a concise interpretation of this graph. Do not include any code or markdown formatting."
                        graph_interpretation = generate_openai_response(ai_graph_prompt)
                        st.info(f"AI Interpretation: {graph_interpretation}")
                        st.session_state.messages.append({"role": "assistant", "content": f"AI Interpretation of {selected_graph_type.replace('_', ' ')}: {graph_interpretation}"})
                        st.session_state['report_content'].append({"type": "text", "content": f"AI Interpretation: {graph_interpretation}"})
                else:
                    st.error(graph_description) # graph_description will contain error message here


        # Statistical Tests
        st.subheader("Perform Statistical Tests")
        test_options = ["None", "independent_t_test", "anova", "chi_squared_test", "pearson_correlation"]
        selected_test_type = st.selectbox("Select Statistical Test:", test_options, key="test_type_select")

        # Dynamic selection for col1 and col2 based on test type
        col1_test = None
        col2_test = None

        if selected_test_type in ["independent_t_test", "anova", "pearson_correlation"]:
            col1_test = st.selectbox(f"Select first column for {selected_test_type.replace('_', ' ').title()}:", all_columns, key="col1_test_select")
            if selected_test_type != "pearson_correlation": # Pearson only needs two columns, others need specific types
                 col2_test = st.selectbox(f"Select second column (grouping/independent) for {selected_test_type.replace('_', ' ').title()}:", all_columns, key="col2_test_select")
            else: # For Pearson, ensure a second column is selected
                col2_options = [col for col in all_columns if col != col1_test]
                if col2_options:
                    col2_test = st.selectbox(f"Select second numerical column for {selected_test_type.replace('_', ' ').title()}:", col2_options, key="col2_test_select_pearson")
                else:
                    st.warning("Please select at least two numerical columns for Pearson Correlation.")
                    col2_test = None # Ensure col2_test is None if not enough options
        elif selected_test_type == "chi_squared_test":
            col1_test = st.selectbox(f"Select first categorical column for {selected_test_type.replace('_', ' ').title()}:", all_columns, key="col1_test_select")
            col2_options = [col for col in all_columns if col != col1_test]
            if col2_options:
                col2_test = st.selectbox(f"Select second categorical column for {selected_test_type.replace('_', ' ').title()}:", col2_options, key="col2_test_select_chi")
            else:
                st.warning("Please select at least two categorical columns for Chi-squared Test.")
                col2_test = None

        if selected_test_type != "None" and col1_test and col2_test:
            if st.button(f"Perform {selected_test_type.replace('_', ' ').title()}"):
                with st.spinner(f"Performing {selected_test_type.replace('_', ' ').title()}..."):
                    test_results_str, structured_results_dfs, error_msg = perform_statistical_test(df, selected_test_type, col1_test, col2_test)
                    if test_results_str:
                        st.subheader(f"Results for {selected_test_type.replace('_', ' ').title()}")
                        st.write(test_results_str)
                        
                        # Display structured results for UI (DataFrames)
                        if structured_results_dfs is not None:
                            if isinstance(structured_results_dfs, pd.DataFrame):
                                st.dataframe(structured_results_dfs, use_container_width=True)
                                # Add to report
                                st.session_state['report_content'].append({"type": "heading", "content": f"Statistical Test: {selected_test_type.replace('_', ' ').title()}", "level": 3})
                                st.session_state['report_content'].append({"type": "text", "content": test_results_str})
                                st.session_state['report_content'].append({"type": "stat_table", "title": f"Summary Table for {selected_test_type.replace('_', ' ').title()}", "dataframe": structured_results_dfs})

                            elif isinstance(structured_results_dfs, tuple): # For T-test and Chi-squared, it's a tuple of DFs
                                if selected_test_type == "independent_t_test":
                                    group_stats_df, test_results_df = structured_results_dfs
                                    st.write("Group Statistics:")
                                    st.dataframe(group_stats_df, use_container_width=True)
                                    st.write("Test Results:")
                                    st.dataframe(test_results_df, use_container_width=True)
                                    # Add to report
                                    st.session_state['report_content'].append({"type": "heading", "content": f"Statistical Test: {selected_test_type.replace('_', ' ').title()}", "level": 3})
                                    st.session_state['report_content'].append({"type": "text", "content": test_results_str})
                                    st.session_state['report_content'].append({"type": "stat_table", "title": "Group Statistics", "dataframe": group_stats_df})
                                    st.session_state['report_content'].append({"type": "stat_table", "title": "Test Results", "dataframe": test_results_df})

                                elif selected_test_type == "chi_squared_test":
                                    observed_df, expected_df, chi2_val, p_val, dof_val = structured_results_dfs
                                    st.write("Observed Frequencies:")
                                    st.dataframe(observed_df, use_container_width=True)
                                    st.write("Expected Frequencies:")
                                    st.dataframe(expected_df, use_container_width=True)
                                    # Add to report
                                    st.session_state['report_content'].append({"type": "heading", "content": f"Statistical Test: {selected_test_type.replace('_', ' ').title()}", "level": 3})
                                    st.session_state['report_content'].append({"type": "text", "content": test_results_str})
                                    st.session_state['report_content'].append({"type": "stat_table", "title": "Observed Frequencies", "dataframe": observed_df})
                                    st.session_state['report_content'].append({"type": "stat_table", "title": "Expected Frequencies", "dataframe": expected_df})


                        # Ask AI for interpretation of the statistical test
                        if st.session_state['openai_client_initialized']:
                            ai_test_prompt = f"A {selected_test_type.replace('_', ' ')} was performed on columns {col1_test} and {col2_test if col2_test else ''}. The results are: {test_results_str}. Provide a concise interpretation of these statistical results. Do not include any code or markdown formatting."
                            test_interpretation = generate_openai_response(ai_test_prompt)
                            st.info(f"AI Interpretation: {test_interpretation}")
                            st.session_state.messages.append({"role": "assistant", "content": f"AI Interpretation of {selected_test_type.replace('_', ' ')}: {test_interpretation}"})
                            st.session_state['report_content'].append({"type": "text", "content": f"AI Interpretation: {test_interpretation}"})
                    else:
                        st.error(error_msg)
        elif selected_test_type != "None":
            st.warning("Please select the required columns for the statistical test.")


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
        "<div style='text-align: center;'>"\
        "SSO Consultants © 2025 | All Rights Reserved."\
        "</div>",
        unsafe_allow_html=True
    )

    # --- In-App Debug Logs ---
    st.expander_debug = st.expander("Show Debug Logs")
    with st.expander_debug:
        if st.button("Clear Debug Logs", key="clear_debug_logs_button"):
            st.session_state['debug_logs'] = []
            st.rerun()
        for log_entry in st.session_state['debug_logs']:
            st.code(log_entry, language='text')

# --- Run the App ---
if not st.session_state['logged_in']:
    st.markdown("") # Empty markdown to prevent footer from being too high on login page.
    # The check_password() function is called above and handles the login UI.
