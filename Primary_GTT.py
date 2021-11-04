import pandas as pd
import warnings
import numpy as np
import yfinance as yf
import pandas_market_calendars as mcal
import matplotlib.pyplot as plt
from pypfopt import (EfficientFrontier, objective_functions, expected_returns, DiscreteAllocation, get_latest_prices)
from datetime import datetime
from Functions import annual_cov, start_date, start_of_month
from scipy.stats import skew, kurtosis

# Ignore warnings
warnings.simplefilter("ignore", UserWarning)  # Ignore UserWarning generated by .add_objective in pypfopt

# Define variables
# I used the ETFs recommended in pg.11 of: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3272080
tickers = ['SPY', 'VBR', 'TLT', 'MDY', 'QQQ', 'GLD']
benchmark = ['SPY']
start = '2005-01-01'
end = '2020-12-31'
training_years = 1  # Number of years for which we calculate the expected return and covariance data
portfolio_value = 5000  # Amount in dollars for initial portfolio value

# Get and process data
# Ticker data
prices = yf.download(tickers=tickers, start=start, end=end)['Adj Close']
daily_ret = np.log(prices / prices.shift(1))[1:]
daily_ret_col = list(daily_ret.columns)
annual_ret = daily_ret.groupby(pd.Grouper(freq='Y')).apply(np.sum)

# Benchmark data
prices_benchmark = yf.download(tickers=benchmark, start=start, end=end)['Adj Close']
prices_benchmark_daily_ret = np.log(prices_benchmark / prices_benchmark.shift(1))[1:]

# FRED data
UNRATE = pd.read_csv('UNRATE.csv', index_col=0).dropna()  # Unemployment Rate
month_start = []
month_end = pd.date_range(start, end, freq='M').strftime('%Y-%m-%d').tolist()
for x in month_end:
    month_start.append(start_of_month(x))
signal_date_range = list(zip(month_start, month_end))
signal_trading_month_start = []
signal_trading_month_end = []
nyse = mcal.get_calendar('NYSE')
for k, v in signal_date_range:
    signal_nyse_trading_date_range = nyse.schedule(k, v)
    signal_nyse_trading_date_range_index = mcal.date_range(signal_nyse_trading_date_range, frequency='1D') \
        .strftime('%Y-%m-%d') \
        .tolist()
    signal_trading_month_start.append(signal_nyse_trading_date_range_index[0])
    signal_trading_month_end.append(signal_nyse_trading_date_range_index[-1])
UNRATE = UNRATE.loc[start:start_of_month(end), :]

# Create list of trading days between start date and end date of training set
# Earliest and latest date of a year
start_years = []
end_years = pd.date_range(start, end, freq='y').strftime('%Y-%m-%d').tolist()
for x in end_years:
    start_years.append(start_date(x))

# Get NYSE trading calendar
nyse = mcal.get_calendar('NYSE')
training_date_range = list(zip(start_years, end_years))

# Perform weights dataframe calculations
weights = pd.DataFrame()
trading_start_dates = []
trading_end_dates = []
allocation_shares = pd.DataFrame()

# Main portfolio calculations happen here. Certain operations in this block require the GLPK_MI solver for CVXPY,
# which you can install by following these instructions: http://cvxopt.org/install/index.html
# You can also check the solvers installed on your environment by using the 'print(cvxpy.installed_solvers())' operation
for k, v in training_date_range:

    # Configure range of a trading year
    nyse_trading_date_range = nyse.schedule(k, v)
    nyse_trading_date_range_index = mcal.date_range(nyse_trading_date_range, frequency='1D')\
        .strftime('%Y-%m-%d')\
        .tolist()
    trading_start_dates.append(nyse_trading_date_range_index[0])
    trading_end_dates.append(nyse_trading_date_range_index[-1])

    # Pull relevant price data for given trading year range
    prices_dataframe = prices.loc[datetime.strptime(str(nyse_trading_date_range_index[0]), '%Y-%m-%d'):
                                  datetime.strptime(str(nyse_trading_date_range_index[-1]), '%Y-%m-%d')]

    # Calculate efficient frontier with given covariance matrix and expected returns
    prices_expected_returns = expected_returns.ema_historical_return(prices_dataframe)
    covariance_matrix = annual_cov(training_years, nyse_trading_date_range_index[0], prices_expected_returns, tickers)
    ef = EfficientFrontier(prices_expected_returns, covariance_matrix)

    # Optimise portfolio and give weights
    raw_weights = ef.max_sharpe()
    cleaned_weights = ef.clean_weights()

    # Append weights to dataframe 'weights'
    cleaned_weights = dict(cleaned_weights)
    weights = weights.append(dict(cleaned_weights), ignore_index=True)

    # Get allocation in shares
    latest_prices = get_latest_prices(prices_dataframe)
    da = DiscreteAllocation(cleaned_weights, latest_prices, total_portfolio_value=portfolio_value)
    allocation, leftover = da.lp_portfolio()
    allocation_shares = allocation_shares.append(dict(allocation), ignore_index=True).set_index(weights.index)

# Clean up weights dataframe
trading_start_years = []
for x in trading_start_dates:
    four_digit_year = x[0:4]
    trading_start_years.append(four_digit_year)
weights.index = trading_start_years

# Create a daily weights dataframe
daily_trading_days = mcal.date_range(nyse.schedule(start, end), frequency='1D')\
        .strftime('%Y-%m-%d')\
        .tolist()
daily_weights = pd.DataFrame(np.repeat(weights.values, 252, axis=0))
daily_weights.columns = weights.columns
daily_weights.drop(daily_weights.tail(4).index, inplace=True)  # Temporary solution, dropping extra 4 rows from df
daily_weights.index = daily_trading_days

# Calculate weighted stock returns
daily_trading_days_modified = daily_trading_days[:-1]  # Temporary solution, dropping extra 1 item from list
daily_ret.index = daily_trading_days_modified
daily_weights_returns = daily_weights.mul(daily_ret).dropna()
daily_weights_returns.columns = daily_ret_col

# Calculate indicators
# Unemployment GTT Model
signal = pd.DataFrame()
UNRATE['UnemploymentMA'] = UNRATE['UNRATE'].rolling(12).mean()
signal['UnemploymentMA'] = np.where(UNRATE['UNRATE'] >= UNRATE['UnemploymentMA'], 1, 0)
signal.index = signal_trading_month_start

# Price indicators
prices['210MA'] = prices['SPY'].rolling(210).mean()
prices['indicator1'] = np.where(prices['SPY'] >= prices['210MA'], 1, 0)
prices_index = prices.index.to_list()
prices_index_dataset = []
for idx, val in enumerate(prices_index):
    for x in signal_trading_month_start:
        if val.strftime('%Y-%m-%d') == x:
            prices_index_dataset.append(prices.iloc[[idx], [-1]])
result = np.reshape(prices_index_dataset, (np.shape(prices_index_dataset)[0], np.shape(prices_index_dataset)[1]))
result_df = pd.DataFrame(result, columns=['indicator1'])
result_df.index = signal_trading_month_start
final_df = pd.concat([signal, result_df], axis=1)

# Signals for the Unemployment GTT Model
conditions_signal3 = [(final_df['UnemploymentMA'] == 1) & (final_df['indicator1'] == 1)]
conditions_signal4 = [(final_df['UnemploymentMA'] == 1) & (final_df['indicator1'] == 0)]
final_df['signal_unemployment'] = np.select(conditions_signal3, ['True'])
final_df['signal_unemployment'] = np.select(conditions_signal4, ['False'])
final_df['signal_unemployment'] = np.where(final_df['UnemploymentMA'] == 0, 'True', 'False')

# Signals dataset: final touches
signal_nyse_trading_date_range = nyse.schedule(signal_trading_month_start[0], signal_trading_month_end[-1])
signal_nyse_trading_date_range_index = mcal.date_range(signal_nyse_trading_date_range, frequency='1D') \
    .strftime('%Y-%m-%d') \
    .tolist()
final_df = final_df.reindex(signal_nyse_trading_date_range_index, method='ffill')

# Create total returns and portfolio value columns
daily_weights_returns['signal'] = final_df['signal_unemployment']
daily_weights_returns['Daily Pct Return'] = daily_weights_returns.sum(axis=1)+1
daily_weights_returns['Daily Pct Return'] = np.where(daily_weights_returns['signal'] == 'False', 1, daily_weights_returns['Daily Pct Return'])
daily_weights_returns = daily_weights_returns.reset_index(drop=False)
daily_weights_returns['Portfolio Value'] = np.nan
daily_weights_returns.at[0, 'Portfolio Value'] = portfolio_value
for i, row in daily_weights_returns.iterrows():  # Loop by iterrows: fight me (or please suggest something better)
    if i == 0:
        daily_weights_returns.loc[i, 'Portfolio Value'] = daily_weights_returns['Portfolio Value'].iat[0]
    else:
        daily_weights_returns.loc[i, 'Portfolio Value'] = daily_weights_returns.loc[i, 'Daily Pct Return'] * \
                                                daily_weights_returns.loc[i - 1, 'Portfolio Value']

# Process benchmark data
prices_benchmark_daily_ret = prices_benchmark_daily_ret.to_frame().reset_index(drop=False)
prices_benchmark_daily_ret['Portfolio Value'] = np.nan
prices_benchmark_daily_ret.loc[[0], ['Portfolio Value']] = portfolio_value
prices_benchmark_daily_ret.rename(columns={prices_benchmark_daily_ret.columns[0]: 'Daily Pct Return'}, inplace=True)
prices_benchmark_daily_ret['Daily Pct Return'] = prices_benchmark_daily_ret.sum(axis=1)+1
for i, row in prices_benchmark_daily_ret.iterrows():
    if i == 0:
        prices_benchmark_daily_ret.loc[i, 'Portfolio Value'] = prices_benchmark_daily_ret['Portfolio Value'].iat[0]
    else:
        prices_benchmark_daily_ret.loc[i, 'Portfolio Value'] = prices_benchmark_daily_ret.loc[i, 'Daily Pct Return'] * \
                                                prices_benchmark_daily_ret.loc[i - 1, 'Portfolio Value']
prices_benchmark_daily_ret['index'] = daily_trading_days_modified

# Plot portfolio value and benchmark
spacing = 10
fig, ax = plt.subplots(figsize=(10, 6))
plt1 = plt.plot(prices_benchmark_daily_ret['index'], prices_benchmark_daily_ret['Portfolio Value'], label=benchmark[0])
plt2 = plt.plot(daily_weights_returns['index'], daily_weights_returns['Portfolio Value'], label='Portfolio Value')
plt.legend()
plt.xticks(rotation=45)

# Reduce number of labels
functions = [plt1, plt2]
for fn in functions:
    visible = ax.xaxis.get_ticklabels()[::spacing]
    for label in ax.xaxis.get_ticklabels():
        if label not in visible:
            label.set_visible(False)

plt.title('Portfolio Performance')
plt.xlabel('Date')
plt.ylabel('Max Sharpe Portfolio Value')
plt.show()

# Calculate portfolio statistics
# Calculate max drawdown
daily_weights_returns.index = daily_trading_days_modified
rolling_max = daily_weights_returns['Portfolio Value'].rolling(252, min_periods=1).max()
daily_drawdown = daily_weights_returns['Portfolio Value']/rolling_max - 1.0
max_daily_drawdown = daily_drawdown.rolling(252, min_periods=1).min()
daily_drawdown.plot()
plt.xticks(rotation=45)
plt.title('Portfolio Max Drawdown')
plt.show()
print('------------------------------------------')
print('Max portfolio drawdown: {:.2%}'.format(round((daily_drawdown.min()), 2)))

# Calculate portfolio return statistics
# Annual portfolio returns
daily_weights_returns['Daily Pct Return'] = daily_weights_returns['Daily Pct Return']-1
daily_weights_returns = daily_weights_returns.reset_index(drop=True)
daily_weights_returns['index'] = pd.to_datetime(daily_weights_returns['index'])
daily_weights_returns.set_index('index', inplace=True)
portfolio_annual_return = daily_weights_returns['Daily Pct Return']\
    .groupby(pd.Grouper(freq='Y')).apply(np.sum).mean()
print('Average annual portfolio return: {:.2%}'.format(portfolio_annual_return))

# Portfolio Sharpe
portfolio_sharpe = daily_weights_returns['Daily Pct Return'].mean() / daily_weights_returns['Daily Pct Return'].std()
portfolio_sharpe_annualised = (250**0.5) * portfolio_sharpe
print('Portfolio Sharpe ratio: {:.2}'.format(portfolio_sharpe_annualised))

# Cumulative returns graph
ax1 = plt.figure().add_axes([0.1, 0.1, 0.8, 0.8])
ax1.hist(daily_weights_returns['Daily Pct Return'], bins=120)
plt.axvline(0, color='r', linestyle='solid', linewidth=1)
plt.text(0.02, 400, 'Skew: {:.2}'.format(skew(daily_weights_returns['Daily Pct Return'])))
plt.text(0.02, 300, 'Kurtosis: {:.4}'.format(kurtosis(daily_weights_returns['Daily Pct Return'])))
ax1.set_xlabel('Portfolio Returns')
ax1.set_ylabel('Freq')
ax1.set_title('Portfolio Returns Histogram')
plt.show()

# Show other portfolio statistics
print('Portfolio returns skew: {:.2}'.format(skew(daily_weights_returns['Daily Pct Return'])))
print('Portfolio returns kurtosis: {:.4}'.format(kurtosis(daily_weights_returns['Daily Pct Return'])))
print('------------------------------------------')
print('-------------------------------------------------------')
print("This year's recommended portfolio weights (by percent):")
print(weights.iloc[-1].to_string())
print('-------------------------------------------------------')
print("This year's recommended portfolio weights (by shares):")
print(allocation_shares.iloc[-1].to_string())
print('-------------------------------------------------------')
