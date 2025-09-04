import pandas as pd
import geopandas as gpd
import plotly.express as px
from pathlib import Path

# 1. Project & folder paths (repo-relative)
PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / 'Data'
VISUALS_DIR = PROJECT_ROOT / 'Visuals'
VISUALS_DIR.mkdir(parents=True, exist_ok=True) 

# Data files (live in the Data/ folder next to the script)
treat_file = DATA_DIR / 'treatment_received.csv'
disord_file = DATA_DIR / 'mental_disorder.csv'
pop_file = DATA_DIR / 'sc-est2023-agesex-civ.csv'
gender_file = DATA_DIR / 'ami_by_gender.csv'

# 2. Load CSVs
treatment = pd.read_csv(treat_file, skiprows=1)
disorder = pd.read_csv(disord_file, skiprows=1)
pop_raw = pd.read_csv(pop_file)
gender_df = pd.read_csv(gender_file)

# 3. Clean NSDUH tables
treatment = treatment.iloc[:, 1:].drop(index=range(1, 6)).reset_index(drop=True)
disorder = disorder.iloc[:, 1:].drop(index=range(1, 6)).reset_index(drop=True)

# 4. Rename columns
column_names = [
    'State', 
    'Estimate_18plus', 'CI_18plus_Lower', 'CI_18plus_Upper',
    'Estimate_18_25', 'CI_18_25_Lower', 'CI_18_25_Upper', 
    'Estimate_26plus', 'CI_26_Lower', 'CI_26_Upper'
]
treatment.columns = column_names
disorder.columns = column_names

# 5. Convert string numbers to float
for df in (treatment, disorder):
    df['Estimate_18plus'] = (
        df['Estimate_18plus']
        .astype(str)
        .str.replace(',', '', regex=False)
        .astype(float)
    )

# 6. Extract national male rates
ami_pct_male = gender_df.loc[gender_df['Sex'] == 'Male', 'AMI_pct'].values[0]
treat_pct_male = gender_df.loc[gender_df['Sex'] == 'Male', 'Treatment_pct'].values[0]

# 7. Merge disorder and treatment data
merged = (
    disorder[['State', 'Estimate_18plus']].rename(columns={'Estimate_18plus': 'Disorder_18plus'})
    .merge(treatment[['State', 'Estimate_18plus']].rename(columns={'Estimate_18plus': 'Treatment_18plus'}), on='State')
)

# 8. Get adult and male adult population
POP_COL = 'POPEST2023_CIV'

adult_pop = (
    pop_raw.loc[(pop_raw['SEX'] == 0) & (pop_raw['AGE'] >= 18)]
    .groupby('NAME', as_index=False)[POP_COL].sum()
    .rename(columns={'NAME': 'State', POP_COL: 'Adults18plus'})
) 

male_pop = (
    pop_raw.loc[(pop_raw['SEX'] == 1) & (pop_raw['AGE'] >= 18)]
    .groupby('NAME', as_index=False)[POP_COL].sum()
    .rename(columns={'NAME': 'State', POP_COL: 'AdultMen18plus'})
)

# 9.Merge population data
merged = (
    merged
    .merge(adult_pop, on='State')
    .merge(male_pop, on='State')
)

# 10. Scale to persons
merged['DisorderCount'] = merged['Disorder_18plus'] * 1_000
merged['TreatmentCount'] = merged['Treatment_18plus'] * 1_000

# 11. Calculate general % of adult population
merged['DisorderPct'] = merged['DisorderCount'] / merged['Adults18plus'] * 100
merged['TreatmentPct'] = merged['TreatmentCount'] / merged['Adults18plus'] * 100

# 12. State-adjustment male estimates

# 12a. National overall AMI prevalence (as % of adults)
overall_ami_pct = (merged['DisorderCount'].sum() / merged['Adults18plus'].sum()) * 100

# 12b. Estimate adjustment factor: how a states's overall AMI compares to national AMI
merged['StateAMIAdjuster'] = (merged['DisorderPct'] / overall_ami_pct).replace([float('inf')], 0)

# 12c. Estimate men with AMI, adjusted by state AMI level
merged['EstimatedMenWithAMI'] = merged['AdultMen18plus'] * (ami_pct_male / 100.0) * merged['StateAMIAdjuster']

# 12d. State treatment-among-diagnosed (overall, not gendered)
merged['StateTreatAmongAMI_overall'] = (merged['TreatmentPct'] / merged['DisorderPct']).replace([float('inf')],0)

# 12e. Scale that overall state rate down to reflect men's lower treatment rate
NATIONAL_TREAT_OVERALL = 50.6
NATIONAL_TREAT_MALE = treat_pct_male #41.6 from CSV
male_scale = (NATIONAL_TREAT_MALE / NATIONAL_TREAT_OVERALL)

merged['MaleTreatAmongAMI_state'] = (merged['StateTreatAmongAMI_overall'] * male_scale)
merged['MaleTreatAmongAMI_state'] = merged['MaleTreatAmongAMI_state'].clip(lower=0, upper=1)

# 12f. Counts of treated men using the state-adjusted male treatment rate
merged['EstimatedMenTreated'] = merged['EstimatedMenWithAMI'] * merged['MaleTreatAmongAMI_state']

# 12g. Final male treatment metrics
merged['EstimatedMaleTreatmentGap'] = merged['EstimatedMenWithAMI'] - merged['EstimatedMenTreated']
merged['MaleTreatmentRate'] = merged['MaleTreatAmongAMI_state'] * 100

# 13. Choropleth maps
states_gdf = gpd.read_file(
    'https://raw.githubusercontent.com/PublicaMundi/MappingAPI/master/data/geojson/us-states.json'
)
map_gdf = states_gdf.merge(merged, left_on='name', right_on='State')

# 13a. Disorder prevalence map
fig_dis = px.choropleth(
    map_gdf,
    geojson=map_gdf.geometry.__geo_interface__,
    locations=map_gdf.index,
    color='DisorderPct',
    hover_name='State',
    hover_data={'DisorderPct': ':.2f'},
    color_continuous_scale='OrRd',
    labels={'DisorderPct': '%'},
    title='Mental Disorder Prevalence (18+) by State',
)
fig_dis.update_geos(fitbounds='locations', visible=False)
fig_dis.write_image(VISUALS_DIR / 'fig_dis.jpg', scale=3)
fig_dis.write_html(VISUALS_DIR / "interactive_disorder-18plus.html", include_plotlyjs="cdn")
fig_dis.show()

# 13b. Treatment received map
fig_treat = px.choropleth(
    map_gdf,
    geojson=map_gdf.geometry.__geo_interface__,
    locations=map_gdf.index,
    color='TreatmentPct',
    hover_name='State',
    hover_data={'TreatmentPct': ':.2f'},
    color_continuous_scale='Blues',
    labels={'TreatmentPct':'%'},
    title='Treatment Received (18+) by State',
)
fig_treat.update_geos(fitbounds='locations', visible=False)
fig_treat.write_image(VISUALS_DIR / 'fig_treat.jpg', scale=3)
fig_treat.write_html(VISUALS_DIR / "interactive_treatment-18plus.html", include_plotlyjs="cdn")
fig_treat.show()

# 14a. Bar chart - Top 10 Male Treatment Rates
top_rate = merged.sort_values(by='MaleTreatmentRate', ascending=False).head(10)

fig_rate = px.bar(
    top_rate,
    x='State',
    y='MaleTreatmentRate',
    title='Top 10 States by Male Treatment Rate (%)',
    labels={'MaleTreatmentRate': 'Treatment Rate (%)'},
    hover_name='State',
    hover_data={'MaleTreatmentRate': ':.2f'}
)

fig_rate.update_traces(texttemplate='', textposition='none')
fig_rate.update_layout(yaxis_range=[0, 100], plot_bgcolor='white')
fig_rate.write_image(VISUALS_DIR / 'fig_rate.jpg', scale=3)
fig_rate.write_html(VISUALS_DIR / "interactive_top10_male_treatment_rate.html", include_plotlyjs="cdn")
fig_rate.show()

# 14b. Bar chart - Bottom 10 States by Male Treatment Rate (downward bars)
bottom_gap = (
    merged.assign(MaleTreatmentGapPct=100 - merged['MaleTreatmentRate'])
    .sort_values(by='MaleTreatmentGapPct', ascending=False)
    .head(10)
)

# Map gap values negative so they point downward
bottom_gap['GapNegative'] = -bottom_gap['MaleTreatmentGapPct']

fig_bottom_gap = px.bar(
    bottom_gap,
    x='State',
    y='GapNegative',
    title='Bottom 10 States - Men not Receiving Treatment (%)',
    labels={'GapNegative': 'No Treatment (%)'},
    hover_name='State',
    hover_data={'MaleTreatmentGapPct': ':.2f'},
)

# Keep hover values positive
fig_bottom_gap.update_traces(
    marker=dict(color='red'),
    hovertemplate='%{x}: %{customdata[0]:.2f}%',
    customdata=bottom_gap[['MaleTreatmentGapPct']].to_numpy()
)

# Flip axis so bars go downward from zero
fig_bottom_gap.update_layout(
    yaxis=dict(range=[-100, 0]),
    plot_bgcolor='white'
)
# Hide the x-axis tick labels
fig_bottom_gap.update_xaxes(showticklabels=False)

# Place state names at the top baseline (y=0)
for st in bottom_gap['State']:
    fig_bottom_gap.add_annotation(
        x=st, y=0, text=st,
        showarrow=False,
        yanchor='bottom',
        yshift=4
    )
fig_bottom_gap.write_image(VISUALS_DIR / 'fig_bottom_gap.jpg', scale=3)
fig_bottom_gap.write_html(VISUALS_DIR / "interactive_bottom10_male_treatment_gap.html", include_plotlyjs="cdn")
fig_bottom_gap.show()