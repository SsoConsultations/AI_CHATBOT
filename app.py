import streamlit as st
import pandas as pd
import io
import os
from openai import OpenAI
from openai import AuthenticationError, APIConnectionError, RateLimitError # Import specific OpenAI errors
from docx import Document
from docx.shared import Inches, Pt
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.section import WD_SECTION_START
import matplotlib.pyplot as plt
import seaborn as sns
import re # For parsing graph requests and markdown bolding
from scipy import stats # For statistical tests

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
            stream=False # Do not stream for this check
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
        print("DEBUG: OpenAI client not initialized or missing.") # Debug print
        return "AI features are not enabled due to API key issues. Please check your OpenAI API key."

    try:
        print(f"DEBUG: Sending prompt to OpenAI (max_tokens=2000):\n{prompt}\n---") # Debug print
        response = client_instance.chat.completions.create( # Use client_instance here
            model=model,
            messages=[
                {"role": "system", "content": "You are a data preprocessing expert. Provide clear, concise, and actionable advice. Do NOT use any markdown formatting (like bolding, italics, code blocks) in your responses. Focus on natural language explanations and interpretations. Always ask the user about their goal for the dataset if not specified, or if a graph is generated, provide an interpretation of that graph. Keep responses concise and to the point."},
                # Only include relevant chat history for AI to avoid exceeding token limits for long conversations
                *st.session_state.messages[-5:], # Send last 5 messages + current prompt to save tokens
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=2000, # Increased max_tokens for debugging
            top_p=1.0,
            frequency_penalty=0.0,
            presence_penalty=0.0
        )
        ai_response_content = response.choices[0].message.content
        print(f"DEBUG: Raw OpenAI response received:\n{ai_response_content}\n---") # Debug print
        return ai_response_content
    except AuthenticationError as e:
        print(f"DEBUG: AuthenticationError: {e}") # Debug print
        st.error("OpenAI API Key is invalid during chat. Please check your .streamlit/secrets.toml file.")
        return "I'm sorry, my connection to the AI failed due to an invalid API key. Please contact support."
    except APIConnectionError as e:
        print(f"DEBUG: APIConnectionError: {e}") # Debug print
        st.error(f"Could not connect to OpenAI API during chat: {e}. Please check your internet connection and firewall settings.")
        return "I'm sorry, I'm having trouble connecting to the AI. Please check your internet connection and try again."
    except RateLimitError as e:
        print(f"DEBUG: RateLimitError: {e}") # Debug print
        st.error("OpenAI API rate limit exceeded during chat. Please try again in a moment.")
        return "I'm sorry, the AI is experiencing high demand. Please try again in a moment."
    except Exception as e:
        print(f"DEBUG: General Exception in generate_openai_response: {e}") # Debug print
        st.error(f"An unexpected error occurred during AI response generation: {e}")
        st.exception(e) # Display the full traceback in the Streamlit UI for debugging
        return "I'm sorry, an unexpected error occurred while generating the AI response. Please try again later."


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

    for item in report_data:
        item_type = item.get("type")
        content = item.get("content")

        if item_type == "heading":
            heading = document.add_heading('', level=item.get("level", 2))
            run = heading.add_run(content)
            run.bold = True # Ensure headings are bold
        elif item_type == "text":
            # Process text to bold numbered list items and markdown bolding
            lines = content.split('\n')
            for line in lines:
                p = document.add_paragraph()
                # Pattern to capture:
                # 1. Start of line with a digit and a dot (e.g., "1. ")
                # 2. Text enclosed in double asterisks (e.g., **bold text**)
                # Split by these, keeping the delimiters.
                # Regex explanation:
                # (^\d+\.\s+): Captures "1. " at the start of a line. Group 1.
                # | : OR
                # (\*\*.*?\*\*): Captures text between ** **. Group 2.
                parts = re.split(r'(^\d+\.\s+)|(\*\*.*?\*\*)', line)
                
                for i, part in enumerate(parts):
                    if part is None or part == '':
                        continue # Skip empty parts from split

                    if re.match(r'^\d+\.\s+', part):
                        # This is a numbered list prefix like "1. ", "2. "
                        # The next part in 'parts' list should be the actual content
                        p.add_run(part) # Add the "1. " part as normal
                        # Check if there's content after the number and if it's not another delimiter
                        if i + 1 < len(parts) and parts[i+1] is not None and not re.match(r'^\d+\.\s+|$', parts[i+1]):
                            # Try to bold the first phrase of the list item
                            # This regex captures text up to a colon, period, or end of line/string
                            phrase_to_bold_match = re.match(r'([^:\.\n]*)(.*)', parts[i+1]) 
                            if phrase_to_bold_match:
                                bold_text = phrase_to_bold_match.group(1).strip()
                                rest_of_line = phrase_to_bold_match.group(2)
                                if bold_text: # Only bold if there's actual text to bold
                                    run = p.add_run(bold_text)
                                    run.bold = True
                                p.add_run(rest_of_line) # Add the rest of the line
                            else:
                                p.add_run(parts[i+1]) # Fallback if no boldable phrase found
                        break # Processed this line, move to next
                    elif part.startswith('**') and part.endswith('**'):
                        # This is a markdown bolded part
                        run = p.add_run(part[2:-2])
                        run.bold = True
                    else:
                        # This is plain text
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
                    missing_percent = row_data[2]
                    stats_summary = row_data[3]

                    # Combine column name and short data type for the first cell
                    combined_col_info = f"{col_name}\n({short_dtype})"
                    
                    # Create the new row for the Word table
                    new_row_for_table = [combined_col_info, missing_percent, stats_summary]

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
    Performs the selected statistical test and returns the results as a formatted string.
    """
    results_str = ""
    error_message = None

    try:
        if test_type == "anova":
            if not pd.api.types.is_numeric_dtype(df[col1]):
                error_message = f"ANOVA: Dependent variable '{col1}' must be numerical."
            elif not (pd.api.types.is_object_dtype(df[col2]) or pd.api.types.is_string_dtype(df[col2]) or pd.api.types.is_categorical_dtype(df[col2])):
                error_message = f"ANOVA: Independent variable '{col2}' must be categorical."
            else:
                groups = [df[col1][df[col2] == g].dropna() for g in df[col2].unique()]
                if len(groups) < 2:
                    error_message = f"ANOVA: Independent variable '{col2}' needs at least 2 distinct groups."
                elif any(len(g) == 0 for g in groups):
                    error_message = f"ANOVA: Some groups in '{col2}' have no data for '{col1}' after dropping NaNs."
                else:
                    f_statistic, p_value = stats.f_oneway(*groups)
                    results_str = (
                        f"ANOVA Test Results for '{col1}' by '{col2}':\n"
                        f"  F-statistic: {f_statistic:.4f}\n"
                        f"  P-value: {p_value:.4f}\n"
                        "Interpretation will be provided by the AI."
                    )

        elif test_type == "t_test":
            if not pd.api.types.is_numeric_dtype(df[col1]):
                error_message = f"T-test: Numerical variable '{col1}' must be numerical."
            elif not (pd.api.types.is_object_dtype(df[col2]) or pd.api.types.is_string_dtype(df[col2]) or pd.api.types.is_categorical_dtype(df[col2])):
                error_message = f"T-test: Grouping variable '{col2}' must be categorical."
            else:
                unique_groups = df[col2].unique()
                if len(unique_groups) != 2:
                    error_message = f"T-test: Grouping variable '{col2}' must have exactly 2 distinct groups. Found {len(unique_groups)}."
                else:
                    group1_data = df[col1][df[col2] == unique_groups[0]].dropna()
                    group2_data = df[col1][df[col2] == unique_groups[1]].dropna()
                    if len(group1_data) == 0 or len(group2_data) == 0:
                        error_message = f"T-test: One or both groups have no data for '{col1}' after dropping NaNs."
                    else:
                        t_statistic, p_value = stats.ttest_ind(group1_data, group2_data)
                        results_str = (
                            f"Independent T-test Results for '{col1}' by '{col2}' ({unique_groups[0]} vs {unique_groups[1]}):\n"
                            f"  T-statistic: {t_statistic:.4f}\n"
                            f"  P-value: {p_value:.4f}\n"
                            "Interpretation will be provided by the AI."
                        )

        elif test_type == "chi_squared":
            if not (pd.api.types.is_object_dtype(df[col1]) or pd.api.types.is_string_dtype(df[col1]) or pd.api.types.is_categorical_dtype(df[col1])):
                error_message = f"Chi-squared: Column 1 '{col1}' must be categorical."
            elif not (pd.api.types.is_object_dtype(df[col2]) or pd.api.types.is_string_dtype(df[col2]) or pd.api.types.is_categorical_dtype(df[col2])):
                error_message = f"Chi-squared: Column 2 '{col2}' must be categorical."
            else:
                contingency_table = pd.crosstab(df[col1], df[col2])
                if contingency_table.empty or contingency_table.sum().sum() == 0:
                     error_message = f"Chi-squared: No valid data to form a contingency table for '{col1}' and '{col2}'."
                else:
                    chi2, p_value, dof, expected = stats.chi2_contingency(contingency_table)
                    results_str = (
                        f"Chi-squared Test Results for '{col1}' and '{col2}':\n"
                        f"  Chi-squared statistic: {chi2:.4f}\n"
                        f"  P-value: {p_value:.4f}\n"
                        f"  Degrees of Freedom (dof): {dof}\n"
                        "Interpretation will be provided by the AI."
                    )
        else:
            error_message = "Unsupported statistical test type selected."

    except Exception as e:
        error_message = f"An error occurred during the statistical test: {e}. Please check your column selections and data."
        st.exception(e) # Display traceback in UI for debugging

    return results_str, error_message

# --- Main Application Logic ---
def main_app():
    st.set_page_config(layout="wide", page_title="SSO Data Preprocessing Assistant")

    # Display Logo at the top of the main app
    st.image("SsoLogo.jpg", width=100) # Adjust width as needed
    st.title("Data Preprocessing Assistant")
    st.write(f"Welcome, {st.session_state.get('current_username', 'User')}!")

    # --- OpenAI API Key Check ---
    # Perform this check once after login
    if not st.session_state['openai_client_initialized'] or st.session_state['openai_client'] is None:
        with st.spinner("Verifying OpenAI API key..."):
            if not check_openai_api_key():
                st.stop() # Stop the app if API key is invalid or connection fails

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

                # Generate data summary and store it
                summary_text, summary_table = get_data_summary(df)
                st.session_state['data_summary_text'] = summary_text
                st.session_state['data_summary_table'] = summary_table

                # Add dataset overview to report content
                st.session_state['report_content'].append({"type": "heading", "level": 2, "content": "Dataset Overview"})
                # Split the text summary to only include the general overview, not column details
                overview_text_end_index = summary_text.find("Column Details:")
                if overview_text_end_index != -1:
                    overview_text = summary_text[:overview_text_end_index].strip()
                else:
                    overview_text = summary_text.strip()
                st.session_state['report_content'].append({"type": "text", "content": overview_text})
                st.session_state['report_content'].append({"type": "table", "headers": summary_table[0], "rows": summary_table[1:]}) # Column details table

                # Initial prompt to OpenAI with data summary
                initial_ai_prompt = (
                    "Here is a detailed summary of the user's dataset:\n\n"
                    f"{st.session_state['data_summary_text']}\n\n"
                    "Based on this, what are the initial preprocessing considerations? "
                    "Please also ask the user about their primary goal (e.g., classification, regression, exploratory analysis) for this dataset."
                )
                print(f"DEBUG: Initial AI prompt:\n{initial_ai_prompt}\n---") # Debug print
                with st.spinner("Analyzing data and generating initial insights..."):
                    initial_response = generate_openai_response(initial_ai_prompt)
                    st.session_state.messages.append({"role": "assistant", "content": initial_response})
                    st.session_state.report_content.append({"type": "heading", "level": 2, "content": "Initial Preprocessing Considerations"})
                    st.session_state.report_content.append({"type": "text", "content": initial_response})

            except Exception as e:
                st.error(f"Error reading file: {e}. Please ensure it's a valid CSV or Excel file.")
                st.session_state['df'] = None # Reset df on error
                st.stop() # Stop further execution if file reading fails

    if st.session_state['df'] is not None:
        # --- Persistent Data Preview Area ---
        st.subheader("Current Dataset Preview (Top 5 Rows):")
        
        # Data type short form mapping (re-used from report generation)
        dtype_map = {
            'int64': 'int', # Changed from 'num' to 'int' for clarity in UI
            'float64': 'float', # Changed from 'num' to 'float' for clarity in UI
            'object': 'str',
            'category': 'str',
            'datetime64[ns]': 'date',
            'datetime64': 'date',
            'bool': 'bool' # Boolean type
        }

        # Create a copy of the DataFrame to modify column names for display
        display_df = st.session_state['df'].head().copy()
        new_columns = []
        for col in display_df.columns:
            original_dtype = str(st.session_state['df'][col].dtype) # Get original dtype from full df
            short_dtype = dtype_map.get(original_dtype, 'other')
            new_columns.append(f"{col} ({short_dtype})")
        display_df.columns = new_columns

        st.dataframe(display_df)
        st.write(f"Shape: {st.session_state['df'].shape[0]} rows, {st.session_state['df'].shape[1]} columns")
        st.markdown("---") # Separator for clarity

        st.subheader("Chat with your Data Preprocessing Assistant")

        # Display chat messages from history with different colors
        for message in st.session_state.messages:
            with st.chat_message(message["role"]):
                if message["role"] == "graph" and "content" in message:
                    st.image(message["content"], caption=message.get("caption", ""), use_container_width=True)
                else:
                    st.markdown(message["content"])

        # --- Interactive Data Exploration Controls (New Section) ---
        st.sidebar.markdown("---")
        st.sidebar.header("Generate Specific Graphs")
        
        graph_options = ["Select a graph type", "Histogram", "Box Plot", "Scatter Plot", "Correlation Heatmap", "Bar Chart"]
        selected_graph_type = st.sidebar.selectbox("Choose Graph Type:", graph_options, key="graph_type_select")

        all_columns = st.session_state['df'].columns.tolist()
        numerical_columns = st.session_state['df'].select_dtypes(include=['number']).columns.tolist()
        categorical_columns = st.session_state['df'].select_dtypes(include=['object', 'category']).columns.tolist()

        columns_to_plot = []
        if selected_graph_type in ["Histogram", "Box Plot"]:
            col_options = numerical_columns
            selected_col = st.sidebar.selectbox(f"Select Column for {selected_graph_type}:", ["Select a column"] + col_options, key="single_col_select")
            if selected_col != "Select a column":
                columns_to_plot = [selected_col]
        elif selected_graph_type == "Bar Chart":
            col_options = categorical_columns
            selected_col = st.sidebar.selectbox(f"Select Column for {selected_graph_type}:", ["Select a column"] + col_options, key="single_col_select_cat")
            if selected_col != "Select a column":
                columns_to_plot = [selected_col]
        elif selected_graph_type == "Scatter Plot":
            col1 = st.sidebar.selectbox("Select X-axis Column:", ["Select X"] + numerical_columns, key="scatter_x_select")
            col2 = st.sidebar.selectbox("Select Y-axis Column:", ["Select Y"] + numerical_columns, key="scatter_y_select")
            if col1 != "Select X" and col2 != "Select Y":
                columns_to_plot = [col1, col2]
        # Correlation Heatmap doesn't need column selection here, it uses all numerical

        if st.sidebar.button(f"Generate {selected_graph_type} Chart"): # Changed button label for clarity
            if selected_graph_type == "Select a graph type":
                st.sidebar.warning("Please select a graph type.")
            elif selected_graph_type not in ["Correlation Heatmap"] and not columns_to_plot:
                st.sidebar.warning(f"Please select appropriate columns for {selected_graph_type}.")
            else:
                with st.spinner(f"Generating {selected_graph_type.lower()}..."):
                    img_buffer, graph_desc = generate_and_display_graph(
                        st.session_state['df'], 
                        selected_graph_type.lower().replace(" ", "_"), 
                        columns_to_plot
                    )
                    if img_buffer:
                        # Add graph to messages for persistent display
                        st.session_state.messages.append({"role": "graph", "content": img_buffer, "caption": graph_desc})
                        st.session_state.report_content.append({"type": "heading", "level": 2, "content": f"Graph Generated: {selected_graph_type}"})
                        st.session_state.report_content.append({"type": "image", "content": img_buffer, "caption": graph_desc})
                        
                        # Get AI interpretation of the graph
                        interpretation_prompt = (
                            f"A {selected_graph_type.lower()} of the dataset was just generated. "
                            f"Here is its description: '{graph_desc}'. "
                            "Please provide a concise interpretation of what this graph tells us about the data, "
                            "especially in the context of data preprocessing. Do NOT provide code."
                        )
                        print(f"DEBUG: Graph interpretation prompt:\n{interpretation_prompt}\n---") # Debug print
                        ai_interpretation = generate_openai_response(interpretation_prompt)
                        print(f"DEBUG: Graph interpretation response:\n{ai_interpretation}\n---") # Debug print
                        st.session_state.messages.append({"role": "assistant", "content": ai_interpretation})
                        st.session_state.report_content.append({"type": "text", "content": ai_interpretation})
                    else:
                        st.session_state.messages.append({"role": "assistant", "content": graph_desc}) # graph_desc will contain error message
                        st.session_state.report_content.append({"type": "text", "content": graph_desc})
                st.rerun() # Rerun to display the new message/graph immediately

        # --- Statistical Tests (New Section) ---
        st.sidebar.markdown("---")
        st.sidebar.header("Perform Statistical Tests")

        test_options = ["Select a test", "ANOVA", "Independent T-test", "Chi-squared Test"]
        selected_test = st.sidebar.selectbox("Choose Statistical Test:", test_options, key="stat_test_select")

        stat_col1 = None
        stat_col2 = None

        if selected_test == "ANOVA":
            st.sidebar.info("ANOVA: Compares means of a numerical variable across 2+ categories.")
            # Changed to all_columns based on user feedback
            stat_col1 = st.sidebar.selectbox("Numerical Variable (Dependent):", ["Select column"] + all_columns, key="anova_num_col")
            stat_col2 = st.sidebar.selectbox("Categorical Variable (Independent):", ["Select column"] + all_columns, key="anova_cat_col")
        elif selected_test == "Independent T-test":
            st.sidebar.info("T-test: Compares means of a numerical variable between 2 groups.")
            # Changed to all_columns based on user feedback
            stat_col1 = st.sidebar.selectbox("Numerical Variable:", ["Select column"] + all_columns, key="ttest_num_col")
            stat_col2 = st.sidebar.selectbox("Grouping Variable (2 categories):", ["Select column"] + all_columns, key="ttest_cat_col")
        elif selected_test == "Chi-squared Test":
            st.sidebar.info("Chi-squared: Tests association between two categorical variables.")
            # Changed to all_columns based on user feedback
            stat_col1 = st.sidebar.selectbox("Categorical Variable 1:", ["Select column"] + all_columns, key="chi2_cat1_col")
            stat_col2 = st.sidebar.selectbox("Categorical Variable 2:", ["Select column"] + all_columns, key="chi2_cat2_col")
        
        if st.sidebar.button(f"Run {selected_test}"):
            if selected_test == "Select a test":
                st.sidebar.warning("Please select a statistical test to run.")
            elif stat_col1 == "Select column" or (selected_test != "Chi-squared Test" and stat_col2 == "Select column"):
                 st.sidebar.warning("Please select all required columns for the chosen test.")
            elif selected_test == "Chi-squared Test" and stat_col2 == "Select column": # Specific check for Chi-squared
                 st.sidebar.warning("Please select both categorical columns for the Chi-squared test.")
            else:
                with st.spinner(f"Running {selected_test}..."):
                    test_results_str, test_error = perform_statistical_test(
                        st.session_state['df'], 
                        selected_test.lower().replace(" ", "_").replace("-", "_"), # Convert to snake_case
                        stat_col1, 
                        stat_col2
                    )
                    if test_error:
                        st.session_state.messages.append({"role": "assistant", "content": test_error})
                        st.session_state.report_content.append({"type": "text", "content": f"Statistical Test Error ({selected_test}): {test_error}"})
                    else:
                        st.session_state.messages.append({"role": "assistant", "content": test_results_str})
                        st.session_state.report_content.append({"type": "heading", "level": 2, "content": f"Statistical Test: {selected_test}"})
                        st.session_state.report_content.append({"type": "text", "content": test_results_str})

                        # Get AI interpretation of the test results
                        interpretation_prompt = (
                            f"A {selected_test} was just performed with the following results:\n"
                            f"{test_results_str}\n"
                            "Please provide a concise, plain-language interpretation of these results, "
                            "focusing on what the p-value means and the implications for the relationship between the variables. "
                            "Do NOT provide code or markdown formatting."
                        )
                        print(f"DEBUG: Stat test interpretation prompt:\n{interpretation_prompt}\n---") # Debug print
                        ai_interpretation = generate_openai_response(interpretation_prompt)
                        print(f"DEBUG: Stat test interpretation response:\n{ai_interpretation}\n---") # Debug print
                        st.session_state.messages.append({"role": "assistant", "content": ai_interpretation})
                        st.session_state.report_content.append({"type": "text", "content": ai_interpretation})
                st.rerun()

        # Chat input box - positioned before the footer
        if prompt := st.chat_input("Ask about preprocessing or analysis..."):
            st.session_state.messages.append({"role": "user", "content": prompt})
            with st.chat_message("user"):
                st.markdown(prompt)

            # Update user goal if explicitly stated in the prompt
            prompt_lower = prompt.lower()
            if "my goal is" in prompt_lower or "i want to do" in prompt_lower or "my objective is" in prompt_lower:
                st.session_state['user_goal'] = prompt # Simple capture for now
                st.session_state.report_content.append({"type": "heading", "level": 2, "content": "User's Stated Goal"})
                st.session_state.report_content.append({"type": "text", "content": prompt})

            # Construct prompt for OpenAI, including data summary and full chat history
            full_prompt = (
                f"Dataset Summary:\n{st.session_state['data_summary_text']}\n\n"
                "Conversation History:\n" + "\n".join([f"{m['role']}: {m['content']}" for m in st.session_state.messages]) +
                f"\n\nUser's current message: {prompt}\n\n"
                "Based on the dataset summary and our conversation, provide tailored preprocessing advice, "
                "including explanations. Do NOT provide Python code snippets. "
                "If the user has stated a goal, ensure your advice aligns with it."
            )
            print(f"DEBUG: General chat prompt:\n{full_prompt}\n---") # Debug print
            with st.spinner("Generating response..."):
                response = generate_openai_response(full_prompt)
                st.session_state.messages.append({"role": "assistant", "content": response})
                st.session_state.report_content.append({"type": "text", "content": response}) # Log AI responses for report
            st.rerun() # Rerun to display the new message/graph

    st.sidebar.markdown("---")
    st.sidebar.header("Report & Actions")

    # Add a "Reset Chat" button in the sidebar
    if st.sidebar.button("Reset Chat"):
        st.session_state['messages'] = []
        st.session_state['report_content'] = []
        st.session_state['user_goal'] = "Not specified"
        # If a file is uploaded, re-trigger initial analysis
        if st.session_state['df'] is not None:
            summary_text, summary_table = get_data_summary(st.session_state['df'])
            st.session_state['data_summary_text'] = summary_text
            st.session_state['data_summary_table'] = summary_table
            st.session_state['report_content'].append({"type": "heading", "level": 2, "content": "Dataset Overview"})
            overview_text_end_index = summary_text.find("Column Details:")
            if overview_text_end_index != -1:
                overview_text = summary_text[:overview_text_end_index].strip()
            else:
                overview_text = st.session_state['data_summary_text'].split("Column Details:")[0].strip() # Fallback
            st.session_state['report_content'].append({"type": "text", "content": overview_text})
            st.session_state['report_content'].append({"type": "table", "headers": summary_table[0], "rows": summary_table[1:]})
            
            initial_ai_prompt = (
                "Here is a detailed summary of the user's dataset:\n\n"
                f"{st.session_state['data_summary_text']}\n\n"
                "Based on this, what are the initial preprocessing considerations? "
                "Please also ask the user about their primary goal (e.g., classification, regression, exploratory analysis) for this dataset."
            )
            print(f"DEBUG: Reset chat initial AI prompt:\n{initial_ai_prompt}\n---") # Debug print
            with st.spinner("Analyzing data and generating initial insights..."):
                initial_response = generate_openai_response(initial_ai_prompt)
                st.session_state.messages.append({"role": "assistant", "content": initial_response})
                st.session_state.report_content.append({"type": "heading", "level": 2, "content": "Initial Preprocessing Considerations"})
                st.session_state.report_content.append({"type": "text", "content": initial_response})
        st.rerun()


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

    # Logout button in sidebar (placed after footer for consistent sidebar flow)
    if st.sidebar.button("Logout"):
        st.session_state['logged_in'] = False
        st.session_state['current_username'] = None
        st.session_state['df'] = None
        st.session_state['data_summary_text'] = ""
        st.session_state['data_summary_table'] = []
        st.session_state['messages'] = []
        st.session_state['report_content'] = []
        st.session_state['user_goal'] = "Not specified"
        if 'uploaded_file_name' in st.session_state:
            del st.session_state['uploaded_file_name']
        st.session_state['openai_client_initialized'] = False # Reset OpenAI client status on logout
        st.session_state['openai_client'] = None # Clear OpenAI client instance on logout
        st.rerun()

# --- Run the App ---
if not st.session_state['logged_in']:
    check_password()
else:
    main_app()
