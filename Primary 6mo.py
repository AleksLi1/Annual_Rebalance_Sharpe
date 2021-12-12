import pandas as pd
import warnings
import numpy as np
import pandas_market_calendars as mcal
import matplotlib.pyplot as plt
from pypfopt import (EfficientFrontier, expected_returns, DiscreteAllocation, get_latest_prices)
from datetime import datetime
from Functions import start_date, start_date_six, semi_annual_cov
from scipy.stats import skew, kurtosis

# Ignore warnings
warnings.simplefilter("ignore", UserWarning)  # Ignore UserWarning generated by .add_objective in pypfopt

# Define variables
# I used some of the ETFs recommended in pg.11 of: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3272080
tickers = ['SPY', 'VBR', 'TLT', 'MDY', 'QQQ', 'GLD']
benchmark = ['SPY']
start = '2004-12-01'  # Include an extra previous month for dataframe calculations
start_real = '2005-01-01'  # Date to start calculations
end = '2021-12-31'  # Last day of the last year within the dataset
end_real = '2021-06-31'  # Date to end calculations
training_years = 1  # deprecated: Number of years for which we calculate the expected return and covariance data
portfolio_value = 5000  # Amount in dollars for initial portfolio value

# Get and process data
# Ticker data
prices = pd.read_csv('data/price_data_6mo.csv', index_col=0).dropna()
prices.index = pd.to_datetime(prices.index)
daily_ret = np.log(prices / prices.shift(1))[1:]
daily_ret_col = list(daily_ret.columns)
half_ret = daily_ret.groupby(pd.Grouper(freq='6M')).apply(np.sum)

# Benchmark data
prices_benchmark = prices[benchmark]
prices_benchmark_daily_ret = np.log(prices_benchmark / prices_benchmark.shift(1))[1:]

# Create list of trading days between start date and end date of training set
start_dates = []
mid_dates = []
end_dates = pd.date_range(start, end, freq='Y').strftime('%Y-%m-%d').tolist()
for x in end_dates:
    start_dates.append(start_date(x))

for x in end_dates:
    mid_dates.append(start_date_six(x))

# Drop the extra month we've added in the parameters
del start_dates[0]
del mid_dates[0]
del end_dates[0]

training_date_range = list(zip(start_dates, mid_dates, end_dates))

# Get NYSE trading calendar
nyse = mcal.get_calendar('NYSE')

# Perform weights dataframe calculations
weights = pd.DataFrame()
trading_start_dates = []
trading_mid_dates = []
trading_end_dates = []
allocation_shares = pd.DataFrame()

# Main portfolio calculations happen here. Certain operations in this block require the GLPK_MI solver for CVXPY,
# which you can install by following these instructions: http://cvxopt.org/install/index.html
# You can also check the solvers installed on your environment by using the 'print(cvxpy.installed_solvers())' operation
for x, y, z in training_date_range:

    # Configure range of H1
    nyse_trading_date_range_H1 = nyse.schedule(x, y)
    nyse_trading_date_range_index_H1 = mcal.date_range(nyse_trading_date_range_H1, frequency='1D')\
        .strftime('%Y-%m-%d')\
        .tolist()
    trading_start_dates.append(nyse_trading_date_range_index_H1[0])
    trading_mid_dates.append(nyse_trading_date_range_index_H1[-1])

    # Configure range of H2
    nyse_trading_date_range_H2 = nyse.schedule(y, z)
    nyse_trading_date_range_index_H2 = mcal.date_range(nyse_trading_date_range_H2, frequency='1D')\
        .strftime('%Y-%m-%d')\
        .tolist()
    trading_end_dates.append(nyse_trading_date_range_index_H2[-1])

    # Pull relevant price data for given H1
    prices_dataframe_H1 = prices.loc[datetime.strptime(str(nyse_trading_date_range_index_H1[0]), '%Y-%m-%d'):
                                     datetime.strptime(str(nyse_trading_date_range_index_H1[-1]), '%Y-%m-%d')]

    # Pull relevant price data for given H2
    prices_dataframe_H2 = prices.loc[datetime.strptime(str(nyse_trading_date_range_index_H2[0]), '%Y-%m-%d'):
                                     datetime.strptime(str(nyse_trading_date_range_index_H2[-1]), '%Y-%m-%d')]

    # Calculate efficient frontier with given covariance matrix and expected returns for H1
    prices_expected_returns_H1 = expected_returns.ema_historical_return(prices_dataframe_H1)
    covariance_matrix_H1 = semi_annual_cov(training_years, nyse_trading_date_range_index_H1[0],
                                           prices_expected_returns_H1, tickers)
    ef_H1 = EfficientFrontier(prices_expected_returns_H1, covariance_matrix_H1)

    # Calculate efficient frontier with given covariance matrix and expected returns for H2
    prices_expected_returns_H2 = expected_returns.ema_historical_return(prices_dataframe_H2)
    covariance_matrix_H2 = semi_annual_cov(training_years, nyse_trading_date_range_index_H2[0],
                                           prices_expected_returns_H1, tickers)
    ef_H2 = EfficientFrontier(prices_expected_returns_H2, covariance_matrix_H2)

    # Optimise portfolio and give weights for H1
    raw_weights_H1 = ef_H1.max_sharpe()
    cleaned_weights_H1 = ef_H1.clean_weights()

    # Optimise portfolio and give weights for H2
    raw_weights_H2 = ef_H2.max_sharpe()
    cleaned_weights_H2 = ef_H2.clean_weights()

    # Append weights to dataframe 'weights'
    cleaned_weights_H1 = dict(cleaned_weights_H1)
    cleaned_weights_H2 = dict(cleaned_weights_H2)
    weights = weights.append(dict(cleaned_weights_H1), ignore_index=True)
    weights = weights.append(dict(cleaned_weights_H2), ignore_index=True)

    # Get allocation in shares
    latest_prices_H1 = get_latest_prices(prices_dataframe_H1)
    latest_prices_H2 = get_latest_prices(prices_dataframe_H2)
    da_H1 = DiscreteAllocation(cleaned_weights_H1, latest_prices_H1, total_portfolio_value=portfolio_value)
    allocation_H1, leftover_H1 = da_H1.lp_portfolio()
    da_H2 = DiscreteAllocation(cleaned_weights_H2, latest_prices_H2, total_portfolio_value=portfolio_value)
    allocation_H2, leftover_H2 = da_H2.lp_portfolio()
    allocation_shares = allocation_shares.append(dict(allocation_H1), ignore_index=True)
    allocation_shares = allocation_shares.append(dict(allocation_H2), ignore_index=True)

# Clean up weights dataframe
trading_dates_draft = list(zip(trading_start_dates, trading_mid_dates))
trading_dates_final = []
for x, y in trading_dates_draft:
    trading_dates_final.append(x)
    trading_dates_final.append(y)
weights.index = trading_dates_final

# Create a daily weights dataframe
daily_trading_days = mcal.date_range(nyse.schedule(trading_dates_final[0], trading_dates_final[-1]), frequency='1D')\
        .strftime('%Y-%m-%d')\
        .tolist()
daily_weights = pd.DataFrame(np.repeat(weights.values, 126, axis=0))
daily_weights.columns = weights.columns
offset = len(daily_weights.index) - len(daily_trading_days)
daily_weights = daily_weights.iloc[:-offset]
daily_weights.index = daily_trading_days

# Calculate weighted stock returns
daily_ret.index = daily_trading_days
daily_weights_returns = daily_weights.mul(daily_ret).dropna()
daily_weights_returns.columns = daily_ret_col

# Create total returns and portfolio value columns
daily_weights_returns['Daily Pct Return'] = daily_weights_returns.sum(axis=1)+1
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
prices_benchmark_daily_ret = prices_benchmark_daily_ret.reset_index(drop=False)
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
prices_benchmark_daily_ret['index'] = daily_trading_days

# Plot portfolio value and benchmark
spacing = 10
fig, ax = plt.subplots(figsize=(10, 6))
plt1 = plt.plot(prices_benchmark_daily_ret['index'], prices_benchmark_daily_ret['Portfolio Value'], label=benchmark[0])
plt2 = plt.plot(daily_weights_returns['index'], daily_weights_returns['Portfolio Value'], label='Portfolio Value')
plt.legend()
plt.xticks(rotation=45)

# Reduce number of labels
ax.tick_params(top=False, bottom=False, left=False, right=False, labelleft=True, labelbottom=True)
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
daily_weights_returns.index = daily_trading_days
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
plt.text(0.02, 180, 'Skew: {:.2}'.format(skew(daily_weights_returns['Daily Pct Return'])))
plt.text(0.02, 150, 'Kurtosis: {:.4}'.format(kurtosis(daily_weights_returns['Daily Pct Return'])))
ax1.set_xlabel('Portfolio Returns')
ax1.set_ylabel('Freq')
ax1.set_title('Portfolio Returns Histogram')
plt.show()

# Show other portfolio statistics
print('Portfolio returns skew: {:.2}'.format(skew(daily_weights_returns['Daily Pct Return'])))
print('Portfolio returns kurtosis: {:.4}'.format(kurtosis(daily_weights_returns['Daily Pct Return'])))
print('------------------------------------------')
print('-----------------------------------------------------------------')
print("Recommended portfolio weights (by percent) for the next 6 months:")
print(weights.iloc[-1].to_string())
print('-----------------------------------------------------------------')
print("Recommended portfolio weights (by shares) for the next 6 months:")
print(allocation_shares.iloc[-1].to_string())
print('-----------------------------------------------------------------')